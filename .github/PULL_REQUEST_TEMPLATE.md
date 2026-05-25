## Description

<!-- Describe what this PR changes and why. -->

## Changes

<!-- List the key changes made. -->

-

## How to Test

<!-- How can a reviewer verify this works? Include test commands if applicable. -->

```bash
pytest tests/ -v --tb=short -m "not azure_api and not integration and not slow"
```

## Checklist

- [ ] Tests added/updated for changed code (`tests/test_<module>.py`)
- [ ] `.env.example` updated if new environment variables added
- [ ] `README.md` updated if new env vars or API endpoints added
- [ ] `tests/conftest.py` `mock_settings` updated if new config fields added
- [ ] No API keys or secrets in the diff
- [ ] Docker build passes (`docker build .`)
