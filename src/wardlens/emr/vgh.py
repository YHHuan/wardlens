from __future__ import annotations

import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from wardlens.config import AppSettings
from wardlens.emr.base import (
    EMRError,
    IncompleteFetchError,
    InterfaceChangedError,
    LoginError,
    RequestBudgetExceeded,
)
from wardlens.emr.parsers import PatientListParser, clean_text, same_origin_https, table_to_tsv
from wardlens.models import (
    EMR_TIMEZONE,
    PatientBundle,
    PatientListResult,
    PatientSummary,
    SourceRecord,
)
from wardlens.services.history import (
    TRUNCATION_MARKER,
    bounded_source,
    months_before,
    normalize_result_date,
    source_dates,
)


@dataclass(slots=True)
class _FetchedPage:
    text: str
    url: str


class _RateLimiter:
    """Pace every EMR request and enforce a rolling one-minute ceiling."""

    def __init__(self, delay_seconds: float, max_per_minute: int) -> None:
        self.max_per_minute = max(1, min(max_per_minute, 120))
        self.delay_seconds = max(0.2, delay_seconds, 60.0 / self.max_per_minute)
        self._last_request = 0.0
        self._recent: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                while self._recent and now - self._recent[0] >= 60:
                    self._recent.popleft()
                target = self.delay_seconds + random.uniform(0, min(0.2, self.delay_seconds / 4))
                spacing_wait = max(0.0, target - (now - self._last_request))
                window_wait = 0.0
                if len(self._recent) >= self.max_per_minute:
                    window_wait = max(0.0, 60.01 - (now - self._recent[0]))
                wait_for = max(spacing_wait, window_wait)
                if wait_for <= 0:
                    self._last_request = now
                    self._recent.append(now)
                    return
                time.sleep(wait_for)


class _RequestBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.maximum:
            raise RequestBudgetExceeded(f"單一病人的請求已達安全上限 {self.maximum} 次。")
        self.used += 1


class VGHReadOnlyAdapter:
    """Read-only adapter independently implemented from observed public endpoints.

    The adapter performs no EMR writes and deliberately processes clinical details
    for one selected patient at a time.
    """

    login_page = "https://eip.vghtpe.gov.tw/login.php"
    qemr = "https://web9.vghtpe.gov.tw/emr/qemr/qemr.cfm"
    allowed_hosts = {"eip.vghtpe.gov.tw", "web9.vghtpe.gov.tw"}

    def __init__(
        self, settings: AppSettings | None = None, session: requests.Session | None = None
    ) -> None:
        self.settings = settings or AppSettings()
        self.session = session or requests.Session()
        self._configure_session(self.session)
        self._logged_in = False
        self._limiter = _RateLimiter(
            self.settings.request_delay_seconds,
            self.settings.max_requests_per_minute,
        )
        self._parser = PatientListParser()

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    def login(self, username: str, password: str) -> None:
        username = username.strip()
        if not username or not password:
            raise LoginError("請輸入入口網帳號與密碼。")
        try:
            self._limiter.wait()
            initial = self.session.get(self.login_page, timeout=(10, 30), allow_redirects=True)
            initial.raise_for_status()
            initial_text = self._decode(initial)
            self._validate_login_response(initial, initial_text)
            soup = BeautifulSoup(initial_text, "html.parser")
            csrf = ""
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta:
                csrf = str(meta.get("content", ""))
            if not csrf:
                token_input = soup.find("input", attrs={"name": re.compile("csrf|token", re.I)})
                if token_input:
                    csrf = str(token_input.get("value", ""))

            headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.login_page,
            }
            if csrf:
                headers["X-CSRF-TOKEN"] = csrf
            self._limiter.wait()
            response = self.session.post(
                urljoin(self.login_page, "/login_action.php"),
                data={
                    "login_name": username,
                    "password": password,
                    "loginCheck": "1",
                    "fromAjax": "1",
                },
                headers=headers,
                timeout=(10, 30),
                allow_redirects=True,
            )
            response.raise_for_status()
            response_text = self._decode(response)
            self._validate_login_response(response, response_text)
            try:
                result = response.json()
            except ValueError as exc:
                raise LoginError("入口網登入回應格式已改變。") from exc
            if int(result.get("error", -1)) != 0:
                raise LoginError("登入失敗；請檢查帳密、VPN／院內網路或帳號狀態。")
            redirect = str(result.get("url", "")).strip()
            if redirect:
                dashboard = self._raw_get(urljoin(self.login_page, redirect), use_limiter=True)
                handoff = self._extract_safe_handoff(dashboard.text, dashboard.url)
                if handoff:
                    self._raw_get(handoff, use_limiter=True)
            probe = self._raw_get(
                f"{self.qemr}?action=findPatient&srnId=DRWEBAPP&", use_limiter=True
            )
            if self._looks_like_login(probe.text, probe.url):
                raise LoginError("登入後仍被導回登入頁，可能需要院內 SSO 更新。")
            if BeautifulSoup(probe.text, "html.parser").find(id="patlist") is None:
                raise LoginError("登入後的病人清單 probe 缺少預期結構，已拒絕建立 session。")
            self._logged_in = True
        except LoginError:
            self.logout()
            raise
        except requests.RequestException as exc:
            self.logout()
            raise LoginError("無法連線至入口網；請確認在院內網路或 VPN。") from exc
        finally:
            password = ""  # Do not retain a credential reference beyond this call.

    def logout(self) -> None:
        self._logged_in = False
        try:
            self.session.cookies.clear()
            self.session.close()
        finally:
            self.session = requests.Session()
            self._configure_session(self.session)

    def search_patients(
        self,
        *,
        doctor_id: str = "",
        ward: str = "",
        histnos: list[str] | None = None,
    ) -> PatientListResult:
        self._require_login()
        if histnos:
            requested = list(
                dict.fromkeys(value for item in histnos if (value := re.sub(r"\D", "", item)))
            )
            limited = requested[: self.settings.max_patients]
            all_patients: list[PatientSummary] = []
            warnings: list[str] = []
            pages_fetched = 0
            for histno in limited:
                result = self._search_query(histno=histno)
                pages_fetched += result.pages_fetched
                exact = [patient for patient in result.patients if patient.histno == histno]
                if exact:
                    all_patients.append(exact[0])
                else:
                    warnings.append("有一個指定病歷號未取得精確相符結果。")
                warnings.extend(result.warnings)
            unique = self._deduplicate(all_patients)
            complete = len(limited) == len(requested) and len(unique) == len(requested)
            return PatientListResult(
                patients=unique,
                complete=complete,
                pages_fetched=pages_fetched,
                declared_total=len(requested),
                warnings=warnings,
            )
        return self._search_query(doctor_id=doctor_id.strip(), ward=ward.strip())

    def _search_query(
        self, *, doctor_id: str = "", ward: str = "", histno: str = ""
    ) -> PatientListResult:
        params = {
            "action": "findPatient",
            "wd": ward or "0",
            "histno": histno,
            "pidno": "",
            "namec": "",
            "drid": doctor_id,
            "er": "0",
            "bilqrta": "0",
            "bilqrtdt": "",
            "bildurdt": "0",
            "other": "0",
            "nametype": "",
        }
        current_url: str | None = f"{self.qemr}?{urlencode(params)}"
        patients: list[PatientSummary] = []
        warnings: list[str] = []
        seen_urls: set[str] = set()
        seen_fingerprints: set[str] = set()
        declared_total: int | None = None
        pages = 0
        complete = True

        while current_url and pages < self.settings.max_patient_pages:
            if current_url in seen_urls:
                warnings.append("偵測到重複分頁 URL，已停止以避免無限迴圈。")
                complete = False
                break
            seen_urls.add(current_url)
            page = self._get(current_url)
            parsed = self._parser.parse(page.text, page.url)
            pages += 1
            if parsed.fingerprint in seen_fingerprints:
                warnings.append("偵測到重複頁面內容，清單可能未完整。")
                complete = False
                break
            seen_fingerprints.add(parsed.fingerprint)
            patients.extend(parsed.patients)
            warnings.extend(parsed.warnings)
            if parsed.declared_total is not None:
                declared_total = max(declared_total or 0, parsed.declared_total)
            if len(patients) >= self.settings.max_patients:
                patients = patients[: self.settings.max_patients]
                warnings.append(f"已達安全上限 {self.settings.max_patients} 人，停止抓取。")
                complete = False
                break
            current_url = parsed.next_url
            if current_url and not same_origin_https(current_url, self.allowed_hosts):
                raise InterfaceChangedError("分頁連結離開允許的院內網域，已中止。")
        if current_url and pages >= self.settings.max_patient_pages:
            warnings.append(f"已達分頁上限 {self.settings.max_patient_pages} 頁。")
            complete = False

        patients = self._deduplicate(patients)
        if declared_total is not None and len(patients) != declared_total:
            complete = False
            warnings.append(
                f"頁面宣告 {declared_total} 人，但只取得 {len(patients)} 人；請勿視為完整清單。"
            )
        return PatientListResult(patients, complete, pages, declared_total, warnings)

    def fetch_patient_bundle(self, patient: PatientSummary) -> PatientBundle:
        self._require_login()
        months = max(1, min(int(self.settings.lab_lookback_months), 12))
        bundle = PatientBundle(patient=patient, lookback_months=months)
        budget = _RequestBudget(maximum=40)
        self._enter_patient_context(patient, budget)
        case_no = patient.case_no or self._resolve_case_no(patient, budget)
        lookback = str(months)
        specs = [
            ("profile", "基本資料", self._url("findPba", histno=patient.histno), None, None),
            (
                "admission_note",
                "最新入院病歷",
                self._url("findAdm", histno=patient.histno),
                "admnote",
                None,
            ),
            (
                "progress_note",
                "近期 Progress Notes",
                self._url("findPrg", histno=patient.histno, caseno=case_no),
                None,
                None,
            ),
            ("medication", "目前用藥", self._url("findUd", histno=patient.histno), None, None),
            (
                "lab_chemistry",
                f"生化趨勢（近 {lookback} 個月）",
                self._url("findResd", resdtype="DCHEM", histno=patient.histno, resdtmonth=lookback),
                None,
                "resdtable",
            ),
            (
                "lab_cbc",
                f"CBC 趨勢（近 {lookback} 個月）",
                self._url("findResd", resdtype="DCBC", histno=patient.histno, resdtmonth=lookback),
                None,
                "resdtable",
            ),
        ]
        if case_no:
            # Documented in max870121/VGH_web get_TPR; current encounter only.
            specs.insert(4, (
                "vital_signs", "生命徵象（本次住院 TPR）",
                self._url("findTpr", caseno=case_no), None, "tprlist",
            ))
        else:
            bundle.warnings.append("未取得住院序號，未載入生命徵象 TPR；不能以病歷內舊數值代替目前生命徵象。")
        failed_types: set[str] = set()
        for source_type, title, url, preferred_link_title, expected_id in specs:
            try:
                record = self._fetch_record(
                    budget,
                    source_type=source_type,
                    title=title,
                    url=url,
                    preferred_link_title=preferred_link_title,
                    expected_id=expected_id,
                    patient=patient,
                )
                if record:
                    bundle.add_record(record)
                    if source_type == "vital_signs":
                        bundle.warnings.extend(self._vital_warnings(record.content))
                    if TRUNCATION_MARKER in record.content:
                        bundle.warnings.append(
                            f"{title}：超過來源容量，已標記截短；不可視為完整趨勢。"
                        )
                    if record.status == "empty_unverified":
                        bundle.warnings.append(
                            f"{title}：結構存在但沒有可讀資料列；請回原始 EMR 核對。"
                        )
            except LoginError:
                raise
            except (
                EMRError,
                requests.RequestException,
                AttributeError,
                KeyError,
                ValueError,
            ) as exc:
                failed_types.add(source_type)
                bundle.warnings.append(f"{title}：{self._safe_error(exc)}")
        try:
            self._fetch_report_history(bundle, budget)
        except LoginError:
            raise
        except (EMRError, requests.RequestException, ValueError, AttributeError, KeyError) as exc:
            failed_types.add("report_index")
            bundle.warnings.append(f"歷史報告：{self._safe_error(exc)}")
        if not bundle.records:
            raise IncompleteFetchError("沒有取得任何臨床資料；請勿把空白視為無異常。")
        if {"lab_chemistry", "lab_cbc"}.issubset(failed_types):
            bundle.warnings.append("兩個累積檢驗頁都未通過內容檢查；不可解讀為檢驗正常或沒有資料。")
        if "report_index" in failed_types:
            bundle.warnings.append("報告索引未通過內容檢查；不可解讀為沒有 pending／新報告。")
        if "vital_signs" in failed_types:
            bundle.warnings.append("生命徵象 TPR 未載入；須由床邊／護理系統補充，目前數值未知。")
        return bundle

    @staticmethod
    def _vital_warnings(content: str) -> list[str]:
        labels = {
            "T": r"\b(?:T|temperature)\b|體溫",
            "BP": r"\b(?:BP|SBP|DBP|blood pressure)\b|血壓",
            "HR": r"\b(?:HR|P|pulse|heart rate)\b|脈搏|心跳|心率",
            "RR": r"\b(?:RR|R|respiratory rate)\b|呼吸",
            "SpO₂": r"\bSpO[2₂]\b|血氧|oxygen saturation",
        }
        missing = [name for name, pattern in labels.items() if not re.search(pattern, content, re.I)]
        warnings = ["TPR 是本次住院來源的擷取快照，不保證整段住院完整；各筆以表中量測時間為準，不代表即時生命徵象。氧氣裝置／流量與缺漏欄位須核對床邊。"]
        if missing:
            warnings.append("TPR 未辨識到以下欄位標籤（不代表正常）：" + "、".join(missing))
        if not VGHReadOnlyAdapter._first_date(content):
            warnings.append("TPR 未辨識到日期；無法確立量測新舊，請核對原表時間。")
        return warnings

    def _fetch_report_history(self, bundle: PatientBundle, budget: _RequestBudget) -> None:
        """Known yearly index route; follow only returned same-origin report links."""
        end = bundle.fetched_at.astimezone(EMR_TIMEZONE).date()
        start = months_before(end, bundle.lookback_months or 3)
        candidates: dict[str, tuple[datetime, str]] = {}
        unknown_dates = 0
        for year in range(end.year, start.year - 1, -1):
            budget.consume()
            page = self._get(
                self._url(
                    "findRes",
                    histno=bundle.patient.histno,
                    tmonth=str(year),
                    tdept="ALL",
                )
            )
            soup = BeautifulSoup(page.text, "html.parser")
            table = soup.find(id="reslist")
            if table is None:
                raise InterfaceChangedError("年度報告索引缺少 #reslist。")
            headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
            date_column = next(
                (
                    i
                    for i, header in enumerate(headers)
                    if re.search(r"報告日期|報告時間|report.*date", header, re.I)
                ),
                4,
            )
            lines = ["\t".join(headers)] if headers else []
            for row in table.find_all("tr"):
                cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
                if not cells:
                    continue
                if len(cells) > date_column:
                    cells[date_column] = normalize_result_date(cells[date_column])
                dates = source_dates(cells[date_column]) if len(cells) > date_column else []
                if not dates:
                    unknown_dates += 1
                    # Keep unknown dates visible, never call them outside the window.
                    lines.append("[DATE UNKNOWN] " + "\t".join(cells))
                    continue
                day = dates[0]
                if not start <= day <= end:
                    continue
                lines.append("\t".join(cells))
                anchor = row.find("a", href=True)
                if anchor is None:
                    continue
                url = urljoin(page.url, str(anchor["href"]))
                # Only report links returned by the report index; no generic navigation or writes.
                parsed = urlparse(url)
                linked_ids = parse_qs(parsed.query).get("histno", [])
                if (
                    not same_origin_https(url, self.allowed_hosts)
                    or not parsed.path.startswith("/emr/")
                    or (linked_ids and linked_ids != [bundle.patient.histno])
                    or re.search(
                        r"(?:delete|update|insert|save|submit|logout)",
                        parsed.path + "?" + parsed.query,
                        re.I,
                    )
                    or str(anchor["href"]).startswith(("#", "javascript:"))
                ):
                    bundle.warnings.append("有報告明細連結不符合唯讀院內路徑，已略過。")
                    continue
                candidates.setdefault(
                    url, (datetime(day.year, day.month, day.day, tzinfo=EMR_TIMEZONE), cells[0])
                )
            index_text = "\n".join(lines)
            bundle.add_record(
                SourceRecord(
                    source_type="report_index",
                    title=f"{year} 年報告索引（回溯範圍內）",
                    content=bounded_source(
                        index_text or "[No readable report rows; verify in EMR.]", 200000
                    ),
                    source_url=page.url,
                    source_record_id=sha256(page.url.encode()).hexdigest()[:20],
                    status="index_only" if lines else "empty_unverified",
                )
            )
            # An annual index is not proof that server-side paging was exhausted.
            if soup.find("a", attrs={"rel": "next"}) or soup.find(
                "a", string=re.compile(r"下一頁|Next", re.I)
            ):
                bundle.warnings.append("年度報告索引含下一頁；尚未取得後續頁，歷史範圍不完整。")
        if unknown_dates:
            bundle.warnings.append(
                f"{unknown_dates} 列報告日期未辨識；已保留索引，但未假定屬於回溯範圍。"
            )
        ordered = sorted(candidates.items(), key=lambda item: item[1][0], reverse=True)
        loaded = 0
        for url, (when, title) in ordered[:12]:
            try:
                budget.consume()
                page = self._get(url)
                soup = BeautifulSoup(page.text, "html.parser")
                detail = soup.find(id=re.compile(r"^RSCONTENT$", re.I)) or soup.find("pre")
                if detail is None:
                    raise InterfaceChangedError(
                        "明細缺少 RSCONTENT／pre，不能把選擇頁視為報告全文。"
                    )
                content = self._extract_clinical_text(BeautifulSoup(str(detail), "html.parser"))
                if not content:
                    raise InterfaceChangedError("明細沒有可讀報告文字。")
                bundle.add_record(
                    SourceRecord(
                        source_type="report",
                        title=title,
                        content=content,
                        source_url=page.url,
                        source_record_id=sha256(page.url.encode()).hexdigest()[:20],
                        observed_at=when,
                        status="status_unverified",
                    )
                )
                loaded += 1
            except LoginError:
                raise
            except (EMRError, requests.RequestException) as exc:
                bundle.warnings.append(f"報告明細：{self._safe_error(exc)}")
        bundle.warnings.append(
            f"回溯報告索引可追蹤 {len(ordered)} 個明細連結，已讀取 {loaded} 份全文（每次最多 12 份，優先最新）；"
            "索引不是全文，未載入／未列出不等於沒有報告。報告 final／pending 需核對原文。"
        )

    def _fetch_record(
        self,
        budget: _RequestBudget,
        *,
        source_type: str,
        title: str,
        url: str,
        preferred_link_title: str | None,
        expected_id: str | None,
        patient: PatientSummary,
    ) -> SourceRecord | None:
        budget.consume()
        page = self._get(url)
        soup = BeautifulSoup(page.text, "html.parser")
        if expected_id and soup.find(id=expected_id) is None:
            raise InterfaceChangedError(
                f"頁面缺少預期結構 #{expected_id}，已視為可疑內容而非空資料。"
            )
        if source_type == "profile" and soup.find("table") is None:
            raise InterfaceChangedError("基本資料頁缺少預期表格。")
        final_page = page
        if preferred_link_title:
            anchor = soup.find("a", attrs={"title": preferred_link_title})
            if anchor is None or not anchor.get("href"):
                raise InterfaceChangedError(f"找不到 {preferred_link_title} 明細連結。")
            detail_url = urljoin(page.url, str(anchor["href"]))
            if not same_origin_https(detail_url, self.allowed_hosts):
                raise InterfaceChangedError("病歷明細連結離開允許的院內網域。")
            budget.consume()
            final_page = self._get(detail_url)
            soup = BeautifulSoup(final_page.text, "html.parser")
            if source_type == "admission_note" and soup.find("pre") is None:
                raise InterfaceChangedError("Admission note 明細缺少預期內容。")
        elif source_type in {"progress_note", "medication"}:
            anchor = self._clinical_detail_anchor(soup, patient, source_type)
            if anchor is None or not anchor.get("href"):
                raise InterfaceChangedError("找不到目前 encounter 的明細連結。")
            detail_url = urljoin(page.url, str(anchor["href"]))
            if not same_origin_https(detail_url, self.allowed_hosts):
                raise InterfaceChangedError("病歷明細連結離開允許的院內網域。")
            budget.consume()
            final_page = self._get(detail_url)
            soup = BeautifulSoup(final_page.text, "html.parser")
            if source_type == "medication" and soup.find(id="udorder") is None:
                raise InterfaceChangedError("用藥明細缺少預期結構 #udorder。")
            if source_type == "progress_note" and soup.find("table") is None:
                raise InterfaceChangedError("Progress note 明細缺少預期表格。")

        content = self._extract_clinical_text(soup)
        status = "available"
        if expected_id:
            structure = soup.find(id=expected_id)
            data_rows = [
                row
                for row in structure.find_all("tr")
                if any(clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td"))
            ]
            if not data_rows:
                status = "empty_unverified"
        if not content:
            if expected_id:
                content = (
                    "[Structured source was present but returned no readable rows; verify in EMR.]"
                )
                status = "empty_unverified"
            else:
                raise InterfaceChangedError("頁面存在但找不到可讀內容。")
        # Profile dates commonly describe birth, not when this source was recorded.
        observed_at = (
            None
            if source_type in {"profile", "lab_chemistry", "lab_cbc", "report_index", "vital_signs"}
            else self._first_date(content)
        )
        record_id = sha256(final_page.url.encode("utf-8")).hexdigest()[:20]
        return SourceRecord(
            source_type=source_type,
            title=title,
            content=content,
            source_url=final_page.url,
            source_record_id=record_id,
            observed_at=observed_at,
            status=status,
        )

    def _enter_patient_context(self, patient: PatientSummary, budget: _RequestBudget) -> None:
        """Mirror the two-step context flow required before result endpoints."""

        budget.consume()
        search = self._get(
            self._url(
                "findPatient",
                wd="0",
                histno=patient.histno,
                pidno="",
                namec="",
                drid="",
                er="0",
                bilqrta="0",
                bilqrtdt="",
                bildurdt="0",
                other="0",
                nametype="",
            )
        )
        if patient.histno not in re.sub(r"\s", "", search.text):
            raise IncompleteFetchError("病人 context 搜尋頁未能核對病歷號，已停止載入。")
        budget.consume()
        context = self._get(self._url("findEmr", histno=patient.histno))
        if not clean_text(BeautifulSoup(context.text, "html.parser").get_text(" ", strip=True)):
            raise IncompleteFetchError("病人 context 頁為空，已停止載入。")

    def _resolve_case_no(self, patient: PatientSummary, budget: _RequestBudget) -> str:
        budget.consume()
        page = self._get(self._url("findPbv", histno=patient.histno))
        soup = BeautifulSoup(page.text, "html.parser")
        candidates: list[str] = []
        for option in soup.find_all("option"):
            candidates.extend(
                re.findall(
                    r"(?:caseno|case)=([A-Za-z0-9-]{4,24})", str(option.get("value", "")), re.I
                )
            )
        if not candidates:
            candidates.extend(re.findall(r"(?:caseno|case)=([A-Za-z0-9-]{4,24})", page.text, re.I))
        return candidates[0] if candidates else ""

    def _get(self, url: str) -> _FetchedPage:
        return self._raw_get(url, use_limiter=True)

    def _raw_get(self, url: str, *, use_limiter: bool) -> _FetchedPage:
        if not same_origin_https(url, self.allowed_hosts):
            raise EMRError("已封鎖非院內允許網域的請求。")
        if use_limiter:
            self._limiter.wait()
        try:
            response = self.session.get(url, timeout=(10, 45), allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            if self._logged_in:
                self._discard_session()
                raise LoginError("院內連線中斷；舊 session 已丟棄，請重新登入後再試。") from exc
            raise
        if not same_origin_https(response.url, self.allowed_hosts):
            self._discard_session()
            raise LoginError("院內請求被導向非允許網域；已停止並丟棄 session。")
        text = self._decode(response)
        if self._looks_like_login(text, response.url) and self._logged_in:
            self._discard_session()
            raise LoginError("院內登入 session 已逾時，請重新登入。")
        if self._looks_like_block_or_error(text, response.url):
            self._discard_session()
            raise LoginError("院內系統回傳異常存取／錯誤頁；已停止請求並丟棄 session。")
        return _FetchedPage(text=text, url=response.url)

    @staticmethod
    def _configure_session(session: requests.Session) -> None:
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 WardLens/0.1"
                )
            }
        )

    def _validate_login_response(self, response: requests.Response, text: str) -> None:
        if not same_origin_https(response.url, self.allowed_hosts):
            raise LoginError("入口網回應被導向非允許網域，已停止登入。")
        if self._looks_like_block_or_error(text, response.url):
            raise LoginError("入口網回傳異常存取／錯誤頁，已停止登入。")

    def _discard_session(self) -> None:
        self._logged_in = False
        try:
            self.session.close()
        finally:
            self.session = requests.Session()
            self._configure_session(self.session)

    @staticmethod
    def _decode(response: requests.Response) -> str:
        encodings = []
        if response.encoding and response.encoding.lower() not in {"iso-8859-1", "ascii"}:
            encodings.append(response.encoding)
        encodings.extend(["utf-8", "cp950", "big5"])
        best = ""
        best_score: tuple[int, int] | None = None
        for encoding in dict.fromkeys(encodings):
            try:
                decoded = response.content.decode(encoding, errors="replace")
            except LookupError:
                continue
            score = (decoded.count("�"), decoded.count("Ã") + decoded.count("¤"))
            if best_score is None or score < best_score:
                best, best_score = decoded, score
        return best or response.text

    def _extract_safe_handoff(self, html: str, base_url: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            candidate = urljoin(base_url, str(anchor["href"]))
            if same_origin_https(candidate, self.allowed_hosts):
                return candidate
        for match in re.finditer(r"https://(?:eip|web9)\.vghtpe\.gov\.tw/[^\"'<>\s]+", html):
            candidate = match.group(0)
            if same_origin_https(candidate, self.allowed_hosts):
                return candidate
        script_patterns = (
            r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
            r"window\.open\(\s*['\"]([^'\"]+)['\"]",
        )
        for pattern in script_patterns:
            for match in re.finditer(pattern, html, re.I):
                candidate = urljoin(base_url, match.group(1))
                if same_origin_https(candidate, self.allowed_hosts):
                    return candidate
        stripped = html.strip().strip("'\"; \r\n")
        if stripped.startswith("/"):
            candidate = urljoin(base_url, stripped)
            if same_origin_https(candidate, self.allowed_hosts):
                return candidate
        return None

    @staticmethod
    def _looks_like_login(text: str, url: str) -> bool:
        path = urlparse(url).path.lower()
        lowered = text.lower()
        visible = clean_text(BeautifulSoup(text, "html.parser").get_text(" ", strip=True)).lower()
        markers = (
            "請重新登入",
            "登入逾時",
            "登入已逾時",
            "session expired",
            "please log in again",
        )
        return (
            "login.php" in path
            or ('id="login_name"' in lowered and 'id="password"' in lowered)
            or any(marker in visible for marker in markers)
        )

    @staticmethod
    def _looks_like_block_or_error(text: str, url: str) -> bool:
        path = urlparse(url).path.lower()
        lowered = clean_text(BeautifulSoup(text, "html.parser").get_text(" ", strip=True)).lower()
        if path.endswith("/error.jsp"):
            return True
        strong_markers = (
            "大量異常存取",
            "異常存取行為",
            "access denied",
            "request rejected",
            "temporarily blocked due to unusual activity",
        )
        return any(marker in lowered for marker in strong_markers)

    @staticmethod
    def _clinical_detail_anchor(
        soup: BeautifulSoup, patient: PatientSummary, source_type: str
    ) -> Tag | None:
        anchors = [
            anchor
            for anchor in soup.find_all("a", href=True)
            if not str(anchor["href"]).startswith("#")
        ]
        if patient.case_no:
            for anchor in anchors:
                if patient.case_no in str(anchor["href"]):
                    return anchor
        tokens = ("prg", "progress") if source_type == "progress_note" else ("ud", "order", "drug")
        for anchor in anchors:
            href = str(anchor["href"]).lower()
            if any(token in href for token in tokens):
                return anchor
        return None

    @staticmethod
    def _extract_clinical_text(soup: BeautifulSoup) -> str:
        for unwanted in soup.find_all(["script", "style", "nav"]):
            unwanted.decompose()
        preferred = soup.find(
            id=re.compile(r"RSCONTENT|resdtable|reslist|udorder|tprlist", re.I)
        ) or soup.find("pre")
        container = preferred if preferred is not None else soup
        if container.get("id", "").lower() == "resdtable":
            # Known cumulative-result layout: first data cell is result date.
            # Normalize compact / YY dates here before residual identifier scans,
            # not by interpreting every long number in clinical text as a date.
            for row in container.find_all("tr"):
                cells = row.find_all("td", recursive=False)
                targets = cells[:1] if cells else row.find_all("th", recursive=False)
                for cell in targets:
                    original = clean_text(cell.get_text(" ", strip=True))
                    normalized = normalize_result_date(original)
                    if normalized != original:
                        cell.clear()
                        cell.append(normalized)
        if container.name == "table":
            content = table_to_tsv(container)
        else:
            # Preserve narrative and every top-level table, not just the first
            # header table on a report page. Child tables are handled by parents.
            for table in list(container.find_all("table")):
                if table.find_parent("table") is None:
                    table.replace_with("\n" + table_to_tsv(table) + "\n")
            content = container.get_text("\n", strip=True)
        # Do not collapse TSV separators: they bind dates, results and units.
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return bounded_source("\n".join(lines), 200000)

    @staticmethod
    def _first_date(text: str) -> datetime | None:
        pattern = r"(?<!\d)(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)"
        for match in re.finditer(pattern, text):
            year, month, day = (int(part) for part in match.groups())
            if year == 0:
                continue
            if len(match.group(1)) < 4:
                year += 1911
            try:
                # Never send a naive historical date through OS localtime/mktime:
                # Windows raises OSError(22) for negative Unix timestamps.
                return datetime(year, month, day, tzinfo=EMR_TIMEZONE)
            except ValueError:
                continue
        return None

    def _url(self, action: str, **params: str) -> str:
        values = {"action": action, **{key: value for key, value in params.items() if value}}
        return f"{self.qemr}?{urlencode(values)}"

    def _require_login(self) -> None:
        if not self._logged_in:
            raise LoginError("尚未登入院內系統。")

    @staticmethod
    def _deduplicate(patients: list[PatientSummary]) -> list[PatientSummary]:
        unique: dict[tuple[str, str], PatientSummary] = {}
        for patient in patients:
            unique.setdefault((patient.histno, patient.case_no), patient)
        return list(unique.values())

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(
            exc, (InterfaceChangedError, RequestBudgetExceeded, LoginError, IncompleteFetchError)
        ):
            return str(exc)
        if isinstance(exc, requests.RequestException):
            return "網路請求失敗或逾時。"
        return "解析失敗；院內介面可能已變更。"
