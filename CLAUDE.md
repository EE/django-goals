# Development

Tests need PostgreSQL.
Copy `example.env` to `.env` and point `DATABASE_URL` at your server.

    poetry install
    poetry run pytest
    poetry run mypy .
    poetry run flake8

pytest runs with `--reuse-db`;
add `--create-db` when an interrupted run left rows behind.

CI runs pytest and mypy for each entry of the `pytest.yml` matrix:
the locked dependencies with one package swapped.
To reproduce an entry, run `poetry run -- pip install 'django==5.2.*'`
(without `--`, poetry parses pip's options),
then `poetry install --sync` to go back.
