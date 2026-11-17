@echo off
echo =========================================
echo RUNNING INSTITUTE PORTAL MIGRATIONS...
echo =========================================
docker compose run --rm -e PYTHONPATH=/app institute-organization-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app institute-identity-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app institute-academics-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app institute-enrollment-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app institute-paper-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app institute-exam-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app institute-operations-service alembic upgrade head

echo =========================================
echo RUNNING STUDENT PORTAL MIGRATIONS...
echo =========================================
docker compose run --rm -e PYTHONPATH=/app student-authentication-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-profile-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-enrollment-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-test-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-attempt-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-result-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-ranking-service alembic upgrade head
docker compose run --rm -e PYTHONPATH=/app student-notification-service alembic upgrade head

echo All migrations completed!
pause