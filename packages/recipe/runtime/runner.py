from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import alloy_v10_shadow as a
base=ROOT/'models/alloy_v10';code=base/'code'
a.FOUNDATION_DIR=base/'foundation'
sys.path.insert(0,str(base/'foundation'))
sys.argv.extend(['--checkpoint',str(base/'model_state.pt'),'--preprocessor',str(base/'preprocessor.json'),
                 '--descriptor-properties',str(code/'descriptor_properties.json'),'--code-dir',str(code),
                 '--support-code-dir',str(code),'--v2-runtime',str(code)])
raise SystemExit(a.main())
