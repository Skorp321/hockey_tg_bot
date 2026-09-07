from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import Config
from .database import engine
from .web.routes import router as web_router
from .web.security import NotAuthenticated

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    # docs_url/redoc_url/openapi_url отключены: Flask на этих путях ничего не отдавал,
    # поверхность URL сохраняем один в один.
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    app.add_middleware(
        SessionMiddleware,
        secret_key=Config.SECRET_KEY,
        session_cookie="session",
        same_site="lax",
        https_only=False,
        max_age=None,  # кука живёт до закрытия браузера, как было у Flask
    )

    # Бот кладётся сюда из run.py. None по умолчанию, чтобы атрибут существовал,
    # даже если бот не поднялся за все попытки.
    app.state.bot = None
    app.state.bot_app = None

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request, exc):
        return RedirectResponse("/login", status_code=302)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc):
        # Flask отдавал битый ввод как {'success': False, 'error': ...} с кодом 400.
        # Дефолт FastAPI - 422 {'detail': [...]} - сломал бы фронтенд.
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(exc)},
        )

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.include_router(web_router)

    return app
