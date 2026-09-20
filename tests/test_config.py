import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from goalforge.config import read_env, settings
from goalforge.cli import main


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / '.env'

    def tearDown(self):
        self.tmp.cleanup()

    def test_quotes_comments_and_literal_values(self):
        self.path.write_text('''# comment
export LLM_API_KEY="key # literal" # comment
LLM_MODEL='provider/model'
LLM_BASE_URL=https://example.com/v1 # endpoint
EMPTY= # no value
LITERAL=$(do-not-execute)${OTHER}
''')
        values = read_env(self.path)
        self.assertEqual(values['LLM_API_KEY'], 'key # literal')
        self.assertEqual(values['LLM_MODEL'], 'provider/model')
        self.assertEqual(values['LLM_BASE_URL'], 'https://example.com/v1')
        self.assertEqual(values['EMPTY'], '')
        self.assertEqual(values['LITERAL'], '$(do-not-execute)${OTHER}')

    def test_environment_wins_without_exporting_file_values(self):
        self.path.write_text('LLM_MODEL=file-model\nLLM_API_KEY=file-secret\n')
        with patch.dict(os.environ, {'LLM_MODEL': 'env-model'}, clear=True):
            config = settings(self.path)
            self.assertEqual(config['LLM_MODEL'], 'env-model')
            self.assertEqual(config['LLM_API_KEY'], 'file-secret')
            self.assertNotIn('LLM_API_KEY', os.environ)

    def test_default_current_directory_file(self):
        self.path.write_text('LLM_MODEL=current-directory-model\n')
        with patch('goalforge.config.Path.cwd', return_value=self.path.parent), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings()['LLM_MODEL'], 'current-directory-model')

    def test_missing_explicit_file_errors(self):
        self.assertEqual(read_env(self.path), {})
        with self.assertRaises(FileNotFoundError):
            settings(self.path)

    def test_parse_error_does_not_echo_secret(self):
        self.path.write_text('LLM_API_KEY="secret-unclosed\n')
        with self.assertRaises(ValueError) as error:
            read_env(self.path)
        self.assertNotIn('secret-unclosed', str(error.exception))

    def test_inspection_uses_configured_storage(self):
        self.path.write_text(f'GOALFORGE_HOME={self.path.parent}/state\n')
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['list', '--env-file', str(self.path)]), 0)
