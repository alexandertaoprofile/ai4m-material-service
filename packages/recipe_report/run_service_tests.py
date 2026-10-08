"""Use the existing pytest tool with the actual service's dependencies.

Only pytest's pure-Python packages are borrowed; the service environment and
its provider/network/numerical modules remain unchanged. No installation.
"""
import importlib.abc
import importlib.machinery
import os
import sys

TOOL_PACKAGES={'pytest','_pytest','pluggy','iniconfig','packaging','pygments','py',
               'exceptiongroup','tomli'}
TOOL_ROOT='/home/ubuntu/miniconda3/lib/python3.13/site-packages'


class TestToolFinder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if '.' not in fullname and fullname in TOOL_PACKAGES:
            return importlib.machinery.PathFinder.find_spec(fullname,[TOOL_ROOT])
        return None


if __name__=='__main__':
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    sys.meta_path.append(TestToolFinder())
    sys.path.insert(0,os.getcwd())
    import pytest
    print('service Python:',sys.executable,'pytest:',pytest.__version__,flush=True)
    raise SystemExit(pytest.main(sys.argv[1:]))
