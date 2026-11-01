import tempfile,subprocess,json,importlib.util,os
from pathlib import Path
roots=[str(Path(__file__).resolve().parents[1])]
for project in roots:
 script=Path(project)/'scripts/migrate-paths.py';spec=importlib.util.spec_from_file_location('migrate',script);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 with tempfile.TemporaryDirectory(prefix='kagnode-migration-') as tmp:
  root=Path(tmp);journal=root/'journal.json'
  for old,new in mod.MAPPINGS:
   p=root/old.lstrip('/');p.mkdir(parents=True);p.chmod(0o750);(p/'sample').write_text(old);(p/'sample').chmod(0o640);(p/'link').symlink_to('sample')
  if mod.PROJECT=='mail':p=root/'opt/kagnode/kagnode';p.mkdir();(p/'web.py').write_text('# fixture')
  def call(*args,ok=True):
   out=subprocess.run(['python3',str(script),'--root',tmp,'--journal',str(journal),*args],text=True,capture_output=True)
   assert (out.returncode==0)==ok,out.stdout+out.stderr
   return out
  call();assert not journal.exists();call('--apply');call('--apply')
  for old,new in mod.MAPPINGS:
   assert (root/new.lstrip('/')/'sample').read_text()==old
   assert (root/old.lstrip('/')/'sample').read_text()==old
   assert (root/new.lstrip('/')).stat().st_mode&0o777==0o750
  data=json.loads(journal.read_text());record=data['records'][-1];record['stage']='copying';journal.write_text(json.dumps(data));call('--apply')
  for old,new in mod.MAPPINGS:(root/new.lstrip('/')/'after').write_text('post migration write')
  call('--rollback');call('--rollback');call('--apply',ok=False)
  for old,new in mod.MAPPINGS:assert (root/old.lstrip('/')/'sample').read_text()==old
  assert len(list(root.rglob('after')))==len(mod.MAPPINGS)
 print(mod.PROJECT,'apply/idempotence/resume/rollback/new writes/modes: passed')
# Reject unrelated data and destination conflicts without mutation.
for project in roots:
 script=Path(project)/'scripts/migrate-paths.py';spec=importlib.util.spec_from_file_location('migrate',script);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);old,new=mod.MAPPINGS[0];src=root/old.lstrip('/');src.mkdir(parents=True);(src/'keep').write_text('original');dst=root/new.lstrip('/');dst.mkdir(parents=True,exist_ok=True);(dst/'other').write_text('other project')
  out=subprocess.run(['python3',str(script),'--root',tmp,'--apply'],capture_output=True);assert out.returncode!=0;assert (src/'keep').read_text()=='original';assert (dst/'other').read_text()=='other project'
 print(mod.PROJECT,'conflict refusal: passed')

# A root console normally uses umask 077. New shared namespace parents must
# still permit service users to reach public files, without exposing private data.
for project in roots:
 script=Path(project)/'scripts/migrate-paths.py';spec=importlib.util.spec_from_file_location('migrate',script);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 with tempfile.TemporaryDirectory(prefix='kagnode-private-umask-') as tmp:
  root=Path(tmp);journal=root/'journal.json'
  old,new=mod.MAPPINGS[0];src=root/old.lstrip('/');src.mkdir(parents=True);src.chmod(0o755)
  for parent in src.parents:
   if parent==root:break
   parent.chmod(0o755)
  (src/'public').write_text('public');(src/'public').chmod(0o644)
  secret=src/'private';secret.mkdir(mode=0o700);(secret/'credential').write_text('fixture');(secret/'credential').chmod(0o600)
  if mod.PROJECT=='mail':p=src/'kagnode';p.mkdir();(p/'web.py').write_text('# fixture')
  out=subprocess.run(['python3',str(script),'--root',tmp,'--journal',str(journal),'--apply'],capture_output=True,text=True,umask=0o077)
  assert out.returncode==0,out.stdout+out.stderr
  target=root/new.lstrip('/')
  for ancestor in target.parents:
   if ancestor==root:break
   assert ancestor.stat().st_mode&0o111==0o111,'Service cannot traverse '+str(ancestor)
  assert (target/'public').stat().st_mode&0o777==0o644
  assert (target/'private').stat().st_mode&0o777==0o700
  assert (target/'private/credential').stat().st_mode&0o777==0o600
  assert journal.stat().st_mode&0o777==0o600
  # Existing namespace permissions belong to the operator, even if restrictive.
  namespace=root/'existing';namespace.mkdir(mode=0o710);namespace.chmod(0o710)
  mod.make_destination_parents(namespace/'new'/'child')
  assert namespace.stat().st_mode&0o777==0o710
 print(mod.PROJECT,'private umask preserves service traversal, private modes and existing permissions: passed')
