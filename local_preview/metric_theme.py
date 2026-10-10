"""Local test backend extension: durable project themes, separate from business data."""
import json
import re
import sqlite3
from pathlib import Path
from flask import g, request
from flask_smorest import abort

DEFAULT = {'kairon':'#312e81', 'manual':'#c2410c', 'adjusted':'#0f766e', 'target':'#9333ea'}

def register_metric_theme(app, database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS project_themes (project_id INTEGER PRIMARY KEY, theme TEXT NOT NULL, updated_by INTEGER NOT NULL)')

    @app.route('/api/project-metric-theme', methods=['GET', 'PUT'])
    def project_metric_theme():
        user = getattr(g, 'user', None)
        if not user or not user.is_active:
            abort(401, message='An active account is required.')
        if not user.project or user.project.name.strip().upper() != 'CODING':
            abort(403, message='This theme belongs to the Coding project.')
        with sqlite3.connect(database) as connection:
            if request.method == 'PUT':
                if user.role.role_type.code != 'manager':
                    abort(403, message='Only Coding project managers can change this theme.')
                theme = request.get_json(silent=True)
                if not isinstance(theme, dict) or set(theme) != set(DEFAULT) or not all(isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) for value in theme.values()):
                    abort(400, message='Provide four valid six-digit hex colors.')
                connection.execute('INSERT INTO project_themes VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET theme=excluded.theme, updated_by=excluded.updated_by', (user.project_id, json.dumps(theme), user.id))
            row = connection.execute('SELECT theme FROM project_themes WHERE project_id=?', (user.project_id,)).fetchone()
        return {'status':200, 'message':'Project theme saved.' if request.method == 'PUT' else 'Project theme loaded.', 'data':{'theme':json.loads(row[0]) if row else DEFAULT, 'configured':row is not None}}
