# Security

## Local data

- The application binds its HTTP service to `127.0.0.1` and accepts only loopback Host headers.
- Screenshots stay in a bounded in-memory queue unless debug screenshot saving is explicitly enabled.
- Model API keys are encrypted with Windows DPAPI for the current user and stored only under the local `data` directory.
- The entire `data` directory, environment files, private keys, logs, build output, and executable artifacts are excluded from Git.
- Remote custom model endpoints must use HTTPS. Plain HTTP is accepted only for loopback addresses.

## Reporting a vulnerability

Do not include API keys, screenshots, session exports, or other private data in a public issue. Provide a minimal reproduction with synthetic data.
