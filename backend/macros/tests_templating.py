from django.test import TestCase

from .templating import resolve_macro_template
from .tests_base import MacroTestMixin


class MacroTemplatingTests(MacroTestMixin, TestCase):
    def test_variable_allowlist_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        visitor.name = 'سارا'
        visitor.save(update_fields=['name'])
        resolved = resolve_macro_template('سلام {customer_name}، این پیام از {store_name} است.', conv)
        self.assertIn('سارا', resolved)
        self.assertIn(project.name, resolved)

    def test_unknown_variable_left_literal(self):
        ws, project, visitor, conv = self.make_full_stack()
        resolved = resolve_macro_template('مقدار: {unknown_thing}', conv)
        self.assertEqual(resolved, 'مقدار: {unknown_thing}')

    def test_secret_variable_rejected(self):
        ws, project, visitor, conv = self.make_full_stack()
        for name in ('api_token', 'password', 'secret_key', 'jwt', 'db_password'):
            resolved = resolve_macro_template(f'مقدار: {{{name}}}', conv)
            # Never interpolated — left as the literal placeholder text,
            # since these names are simply not in ALLOWED_VARIABLES.
            self.assertEqual(resolved, f'مقدار: {{{name}}}')

    def test_internal_only_context_never_leaks_into_customer_reply(self):
        """There is no internal-note-only variable in the allowlist at all
        — the same ALLOWED_VARIABLES set is used for every macro action, so
        there is nothing an internal note could expose that a customer
        reply couldn't already safely show.
        """
        from .templating import ALLOWED_VARIABLES
        self.assertNotIn('internal_notes', ALLOWED_VARIABLES)
        self.assertNotIn('secret', ALLOWED_VARIABLES)
        self.assertNotIn('token', ALLOWED_VARIABLES)
