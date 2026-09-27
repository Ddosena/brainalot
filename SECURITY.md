# Security and privacy

Brainalot listens only on `127.0.0.1` and requires a locally generated bearer token
for access to personal data. The Chrome extension connects only to the local
service. iCal feeds are read-only; optional Google OAuth permits event creation, movement and deletion
on explicit user actions. Credentials remain in local service state.

Never commit or share these paths:

- `data/`
- `backups/`
- `.env`
- `reports/`
- Chrome profiles or exported browser storage

Treat a Google Calendar secret iCal URL as a password. If it is exposed, reset
the private address in Google Calendar and reconnect Brainalot.

For a private vulnerability report, contact the repository owner through the
private contact method shown on their GitHub profile. Do not include real vault
files, tokens, calendar URLs, or screenshots with personal data.
