from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config

db = SQLAlchemy()
migrate = Migrate()


def create_app(config_class=Config):
    """Application factory for creating Flask app instances."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)

    with app.app_context():
        from app import models  # noqa: F401
        from app.main import main_bp, init_sock
        app.register_blueprint(main_bp)
        init_sock(app)
        
        # Create database tables if they don't exist
        db.create_all()

    return app


# For backward compatibility
app = create_app()