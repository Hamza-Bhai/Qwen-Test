python -m venv .venv

.venv\Scripts\activate.bat

alembic upgrade head
alembic current

**Server Start Command**
uvicorn app.main:app --reload --port 8000

**Server Restart Command**

uvicorn app.main:app --reload

**Run Tests command**

python -m pytest tests -q -p no:libtmux

**Database Update command**

alembic upgrade head

http://localhost:8000/api/v1/quota
