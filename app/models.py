"""Database models for LightDockerWebUI."""
from app import db


class DockerServer(db.Model):
    """Docker server configuration model."""
    __tablename__ = "docker_server"

    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)  # Friendly name
    host = db.Column(db.String(255), nullable=True)  # Docker host FQDN/IP
    port = db.Column(db.String(10), nullable=True)   # Docker API port
    is_active = db.Column(db.Boolean, default=False)  # Currently selected server

    def __repr__(self):
        return f"<DockerServer {self.display_name}>"

    @property
    def is_configured(self):
        """Check if Docker server is configured for remote access."""
        return bool(self.host and self.port)

    @property
    def connection_url(self):
        """Get the connection URL for this server."""
        if self.is_configured:
            return f"tcp://{self.host}:{self.port}"
        return "unix://var/run/docker.sock"

    @classmethod
    def get_active(cls):
        """Get the currently active Docker server."""
        return cls.query.filter_by(is_active=True).first()

    @classmethod
    def set_active(cls, server_id):
        """Set a server as active and deactivate others."""
        cls.query.update({cls.is_active: False})
        server = cls.query.get(server_id)
        if server:
            server.is_active = True
        db.session.commit()
        return server


# Alias for backward compatibility
Owner = DockerServer
