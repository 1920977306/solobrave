import http.client, json

conn = http.client.HTTPConnection('127.0.0.1', 8081)
conn.request('GET', '/api/openclaw/skills/list', headers={'Authorization': 'Bearer test'})
resp = conn.getresponse()
print('Status:', resp.status)
print('Body:', resp.read().decode()[:300])
