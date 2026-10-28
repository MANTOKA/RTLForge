# Contributing

Use Python 3.10+ and install `pip install -e ".[dev,graph,llm,mcp]"` in a virtual environment.
Run `ruff check .` and `pytest -q`. Docker checks require
`docker build -t rtlforge-sandbox:latest .` and `pytest --run-integration -m integration`.

Keep changes focused. Preserve exact hardware contracts, independent-test isolation,
source-bound evidence and candidate immutability. New repairs require fresh verification.
Never commit credentials, local model profiles, benchmark corpora, memory databases,
generated task directories or proprietary EDA installers/licenses.

Describe the problem, final behavior and validation in a pull request. Distinguish
software tests, real-model runs, functional verification and synthesis results.
Historical benchmark numbers must not be presented as results for new changes.
