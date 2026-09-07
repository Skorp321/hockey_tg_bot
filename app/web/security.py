from fastapi import Request
from fastapi.responses import JSONResponse


class NotAuthenticated(Exception):
    """Бросается require_login; обработчик на уровне приложения превращает в 302 -> /login."""


def require_login(request: Request) -> bool:
    if not request.session.get("logged_in"):
        raise NotAuthenticated()
    return True


def json_error(message, status: int = 500, **extra):
    """Фронтенд читает {'success': False, 'error': ...}.

    Поэтому HTTPException здесь использовать нельзя: он отдаёт {'detail': ...}
    и ломает примерно двадцать fetch-вызовов в шаблонах.
    """
    content = {"success": False, "error": str(message)}
    content.update(extra)
    return JSONResponse(status_code=status, content=content)
