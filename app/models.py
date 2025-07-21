from app import db


class Owner(db.Model):
    __tablename__ = "owner"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String)
    port = db.Column(db.String)
    def __repr__(self):
        return "{}".format(self.name)
