"""The actual gettext path must resolve the welcome heading in every catalogue."""
import ast,gettext,importlib.util,io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro';name='_welcome_locale_checks'
spec=importlib.util.spec_from_file_location(name,P/'__init__.py',submodule_search_locations=[str(P)]);pkg=importlib.util.module_from_spec(spec);sys.modules[name]=pkg;spec.loader.exec_module(pkg)
i18n=__import__(name+'.i18n',fromlist=['*']);KEY='Ready to watch?'
class WelcomeLocalizationTests(unittest.TestCase):
 def test_all_43_catalogues_contain_a_real_translation(self):
  paths=list((P/'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'));self.assertEqual(len(paths),43)
  for p in paths:
   catalog=gettext.GNUTranslations(io.BytesIO(p.read_bytes()));self.assertTrue(catalog._catalog.get(KEY),p.parent.parent.name);self.assertNotEqual(catalog.gettext(KEY),KEY)
 def test_actual_device_language_lookup_and_regional_aliases(self):
  i18n._CATALOGS.clear()
  for p in (P/'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'):
   code=p.parent.parent.name;code={'fr_FR':'fr','sq_AL':'sq','zh':'zh_CN'}.get(code,code)
   with patch.object(i18n,'device_language',return_value=code),patch.object(i18n,'LOCALE_PATH',str(P/'locale')):self.assertNotEqual(i18n._(KEY),KEY,code)
 def test_german_heading_and_existing_turkish_are_correct(self):
  for code,expected in (('de','Bereit zum Fernsehen?'),('tr','İzlemeye hazır mısın?')):
   with patch.object(i18n,'device_language',return_value=code),patch.object(i18n,'LOCALE_PATH',str(P/'locale')):self.assertEqual(i18n._(KEY),expected)
 def test_both_welcome_render_paths_use_gettext(self):
  tree=ast.parse((P/'welcome_design.py').read_bytes());calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_' and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value==KEY]
  self.assertEqual(len(calls),2)
if __name__=='__main__':unittest.main(verbosity=2)
