"""Archive safety tests requiring only Python's standard library."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('data_bootstrap',ROOT/'scripts/bootstrap_data.py')
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)

class BootstrapSafety(unittest.TestCase):
    def package(self,folder,destination='inputs/value.csv',extra=False):
        root=Path(folder)/'project';source=Path(folder)/'download'
        m=root/'inputs/manifests';m.mkdir(parents=True);source.mkdir()
        data=b'value\n1\n'
        row={'destination':destination,'sha256':bootstrap.digest(data),'size_bytes':len(data)}
        with (m/'data_package.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=row);writer.writeheader();writer.writerow(row)
        archive=source/'inputs.zip'
        with ZipFile(archive,'w') as z:
            z.writestr('path_a/'+destination,data)
            if extra:z.writestr('unexpected.csv',data)
        contract={'data_manifest_sha256':bootstrap.digest((m/'data_package.csv').read_bytes()),
                  'assets':[{'filename':archive.name,'size_bytes':archive.stat().st_size,
                             'sha256':bootstrap.digest(archive.read_bytes())}]}
        (m/'release_assets.json').write_text(json.dumps(contract))
        return root,source

    def test_identical_restore_can_be_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            root,source=self.package(folder)
            self.assertEqual(bootstrap.restore(source,root)['files_restored'],1)
            self.assertEqual(bootstrap.restore(source,root)['files_restored'],0)

    def test_parent_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,source=self.package(folder,'../escape.csv')
            with self.assertRaisesRegex(RuntimeError,'Unsafe destination'):bootstrap.restore(source,root)
            self.assertFalse((Path(folder)/'escape.csv').exists())

    def test_unexpected_archive_member_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,source=self.package(folder,extra=True)
            with self.assertRaisesRegex(RuntimeError,'file list'):bootstrap.restore(source,root)
            self.assertFalse((root/'inputs/value.csv').exists())

    def test_protected_existing_input_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root,source=self.package(folder)
            (root/'inputs/value.csv').write_text('preserved')
            with self.assertRaisesRegex(RuntimeError,'Refusing to overwrite'):bootstrap.restore(source,root)
            self.assertEqual((root/'inputs/value.csv').read_text(),'preserved')

if __name__=='__main__':unittest.main()
