import json,subprocess,selectors,time,os
p=subprocess.Popen(['codex','app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
def request(i,method,params):
 p.stdin.write(json.dumps(dict(id=i,method=method,params=params))+'\n');p.stdin.flush()
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if not sel.select(1):continue
  line=p.stdout.readline()
  if not line:raise RuntimeError('server closed')
  msg=json.loads(line)
  if msg.get('id')==i:
   if 'error' in msg:raise RuntimeError(msg['error'])
   return msg['result']
 raise TimeoutError(method)
try:
 request(1,'initialize',{'clientInfo':{'name':'cmux-resume-check','version':'1'}})
 result=request(2,'hooks/list',{'cwds':[os.getcwd()]})
 for entry in result['data']:
  print('errors:',entry['errors'],'warnings:',entry['warnings'])
  for h in entry['hooks']:print(json.dumps({k:h[k] for k in ('key','currentHash','trustStatus','enabled')}))
finally:
 p.terminate()
 try:p.wait(timeout=5)
 except subprocess.TimeoutExpired:p.kill();p.wait()
