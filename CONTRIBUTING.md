# Contributing

Thanks for your interest. The project is small on purpose: it does one job, has no runtime dependencies, and should stay easy for one person to maintain.

## Reporting a problem

Open an issue with:

- your operating system and Python version,
- the pip settings involved (remove hostnames and credentials first),
- what `pipconf2uv` printed and what you expected.

A wrong or missing translation is always a bug worth reporting. Please link the pip or uv documentation that shows the correct behaviour if you can.

## Changing the code

1. Open an issue first for anything larger than a small fix, so the scope can be agreed.
2. Add or update tests. Every translation rule needs a test for the case where it applies and a case where it must not.
3. Keep the standard-library-only rule: no new runtime dependencies.
4. Never print or log credentials. Tests cover redaction; keep them passing.

Development setup and the test command will be documented here once the first code lands.

## Conduct

Be respectful and assume good intent.
