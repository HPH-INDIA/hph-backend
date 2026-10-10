import tempfile
import unittest
from types import SimpleNamespace as NS
from flask import Flask, g
from metric_theme import register_metric_theme, DEFAULT

class ThemeTest(unittest.TestCase):
    def test_persistence_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = directory + '/theme.sqlite3'
            identity = {'user':NS(id=1,is_active=True,project_id=7,project=NS(name='CODING'),role=NS(role_type=NS(code='manager')))}
            def client():
                app = Flask(__name__)
                @app.before_request
                def user(): g.user = identity['user']
                register_metric_theme(app,database)
                return app.test_client()
            api = client()
            self.assertFalse(api.get('/api/project-metric-theme').json['data']['configured'])
            theme = dict(DEFAULT, kairon='#123456')
            self.assertEqual(api.put('/api/project-metric-theme',json=theme).status_code,200)
            identity['user'].role.role_type.code='employee'
            self.assertEqual(client().get('/api/project-metric-theme').json['data']['theme'],theme)
            self.assertEqual(api.put('/api/project-metric-theme',json=DEFAULT).status_code,403)
            identity['user'].role.role_type.code='lead'
            self.assertEqual(api.get('/api/project-metric-theme').json['data']['theme'],theme)
            self.assertEqual(api.put('/api/project-metric-theme',json=DEFAULT).status_code,403)
            identity['user'].role.role_type.code='manager'
            self.assertEqual(api.put('/api/project-metric-theme',json=dict(theme,target='red')).status_code,400)
            identity['user'].project_id=8
            self.assertEqual(api.get('/api/project-metric-theme').json['data']['theme'],DEFAULT)
            identity['user'].project.name='RCM'
            self.assertEqual(api.get('/api/project-metric-theme').status_code,403)
            identity['user']=None
            self.assertEqual(api.get('/api/project-metric-theme').status_code,401)

if __name__ == '__main__': unittest.main()
