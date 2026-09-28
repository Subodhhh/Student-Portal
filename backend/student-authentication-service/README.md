# Student Authentication Service

Simple FastAPI backend for the Student Portal.

## Flow

React Frontend -> FastAPI -> SQLAlchemy -> PostgreSQL/Neon

## Endpoints

- GET /health
- POST /auth/login
- GET /auth/me
- POST /auth/logout

## Setup

1. Create a Python virtual environment.
2. Install requirements.
3. Copy `.env.example` to `.env`.
4. Add the real Neon DATABASE_URL.
5. Run `alembic upgrade head`.
6. Start with `uvicorn app.main:app --reload`.

## Security

- Passwords use Argon2id.
- Login uses server-side sessions.
- Session IDs are sent in HttpOnly cookies.
- Local development uses secure=False because it runs on HTTP.
- Production must use HTTPS and secure=True.
- The current session store is in memory for development.
- Production should move sessions to Redis.
