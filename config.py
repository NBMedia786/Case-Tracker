import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration class."""
    
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False
    
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')
    
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    
    SERPER_API_KEY = os.getenv('SERPER_API_KEY')
    
    SCHEDULER_API_ENABLED = True
    SCHEDULER_TIMEZONE = os.getenv('TIMEZONE', 'UTC')
    
    CASE_STATUSES = ['Open', 'Closed', 'Verdict Reached']


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    
    @property
    def SECRET_KEY(self):
        key = os.getenv('SECRET_KEY')
        if not key:
            raise ValueError("SECRET_KEY must be set in production")
        return key


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Get the configuration based on FLASK_ENV environment variable."""
    env = os.getenv('FLASK_ENV', 'development')
    return config.get(env, config['default'])
