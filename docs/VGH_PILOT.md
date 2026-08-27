# VGH on-site pilot checklist

The repository maintainers do not have live hospital credentials. Complete this checklist with authorized synthetic/test patients before clinical use.

## Stop conditions

Stop immediately if any of the following occurs:

- login or Error.jsp content appears inside a clinical page;
- system reports unusual/mass access;
- declared patient count differs from fetched count;
- two known-good patients return missing expected structures;
- current EMR source and WardLens display disagree in patient identity;
- a network failure is followed by apparently empty data without explicit re-login.

## Minimal pilot

1. Start in Demo and confirm 18 patients render and exports clearly mark unloaded details.
2. Use a test account and an authorized synthetic patient; verify `findPatient → findEmr` context.
3. Compare profile, newest admission note, current encounter progress notes, current medications, DCHEM/DCBC and report index side by side with QEMR.
4. Test a valid short histno without zero-padding.
5. Test a list of 16–25 patients and reconcile the QEMR total.
6. Test a true no-result case: expected table must still be present and output must say `empty_unverified`, not normal.
7. Force a session timeout/network interruption; confirm all requests stop and a fresh login is required.
8. Verify a report containing ordinary text `GAS BLOCKED` is not considered a block page.
9. Inspect deidentified preview using synthetic names, IDs, phone, email, exact dates, copied headers and third-party names.
10. Do not enable cloud AI until hospital privacy/security approval is documented.

## Fixture capture

If HTML must be retained for parser development, use only synthetic/deidentified pages. Remove cookies, identifiers, names, URLs with histno/caseno, timestamps that identify an encounter, free-text signatures and hidden form values before committing. Never upload production HTML to GitHub or an AI service.

## Rollout

Pilot one user and one ward first. Keep no background crawler. Track content-health failures and request counts using metadata only. Any interface change requires a new signed build and repeat validation.
