from app import app, db
from app.models import Owner

@app.shell_context_processor
def make_shell_context():
    return {'db':db, 'Owner': Owner}