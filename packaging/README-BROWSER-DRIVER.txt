This public package does not include Microsoft Edge WebDriver.
Microsoft's included Edge WebDriver EULA restricts redistribution (section 3.e).

For personal testing, use WardLens > 瀏覽器測試 > 從 Microsoft 準備驅動.
Run preparation on a computer with permitted internet access; it downloads the
version pinned in app/wardlens/resources/browser/edge-driver.json and verifies
the archive and executable SHA-256. It does not install software or change policy.
The downloaded driver and its complete license notices stay in this folder.
Keep the prepared folder together when transferring it to your own test computer.
Do not republish that prepared folder as a public release.

The diagnostic checks Edge/driver versions before opening a fresh-profile,
synthetic page. If versions differ, return WardLens-Browser-Diagnostics.txt so a
matching version can be prepared. A passing probe does not validate EMR login.
