from flask import Flask
from .web.routes import web
from .models import Base
from .database import engine, db_session

def create_app():
    app = Flask(__name__, 
                template_folder='templates',  # Указываем путь к шаблонам
                static_folder='static')       # Указываем путь к статическим файлам
    app.config.from_object('app.config.Config')
    
    # Устанавливаем секретный ключ для сессий
    app.secret_key = app.config['SECRET_KEY']
    
    # Регистрируем blueprint
    app.register_blueprint(web)
    
    # Инициализируем базу данных
    Base.metadata.create_all(engine)
    
    # Закрываем сессию при завершении запроса
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db_session.remove()
    
    return app 