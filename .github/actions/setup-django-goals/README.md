# Setup Django Goals Environment

This composite action sets up a complete development environment for Django Goals, including:

## What it does

1. **Python Environment**: Uses the existing `.github/actions/python` action to set up Python 3.13 and Poetry
2. **System Dependencies**: Installs PostgreSQL client and build tools
3. **Database Configuration**: Configures `.env` file with appropriate database connection string
4. **Django Setup**: Runs migrations and performs basic health checks
5. **Verification**: Tests that the environment is ready for development

## Usage

### In a workflow

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ./.github/actions/setup-django-goals
```

### With PostgreSQL service

```yaml
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_PASSWORD: postgrespass
      POSTGRES_DB: django_goals
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
    ports:
      - 5432:5432

steps:
  - uses: actions/checkout@v4
  - uses: ./.github/actions/setup-django-goals
```

## Dependencies

- Requires PostgreSQL service to be available (see example above)
- Depends on `.github/actions/python` composite action
- Expects `example.env` file to exist in repository root

## What it configures

- Creates `.env` file with `DATABASE_URL=postgresql://postgres:postgrespass@localhost:5432/django_goals`
- Installs all Python dependencies via Poetry
- Runs Django migrations
- Verifies Django Goals can be imported and used

This action is used by:
- `.github/workflows/copilot-setup-steps.yml` for GitHub Copilot environment setup
- `.github/workflows/copilot-setup-test.yml` for testing the setup