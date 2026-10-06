# Verification

Checked on Windows on October 7, 2026:

- Go bridge compiled for Windows x64.
- Measured download progress events were tested using a simulated backend.
- Python tests cover two-factor verification, cancellation, credential transport, measured USB upload progress, final installation status, remote staging cleanup and connection cleanup on failure.
- The actual Windows interface was rendered and inspected in initial, download, success, error and two-factor-verification states.
- The interface includes instructions to keep Apple Devices open throughout the process.
- The original local Musi IPA was identified successfully by bundle ID.
- The preceding bridge passed a live Apple configuration and SAP-signing diagnostic without credentials. The redesign retains that authentication implementation.

The previous version's real account download and installation were reported working by the user. This redesigned version has not performed a new live-account download or installation during validation. UI previews and backend tests do not establish availability for every Apple account.
