import http.client, json

# Step 1: Login
conn = http.client.HTTPConnection('127.0.0.1', 8081)
conn.request('POST', '/api/auth/login', 
    body=json.dumps({'username':'admin','password':'admin123'}),
    headers={'Content-Type':'application/json'})
resp = conn.getresponse()
data = json.loads(resp.read().decode())
token = data.get('token','')
print('Login:', data.get('user',{}).get('username'))

# Step 2: List skills
conn2 = http.client.HTTPConnection('127.0.0.1', 8081)
conn2.request('GET', '/api/openclaw/skills/list', headers={'Authorization': 'Bearer ' + token})
resp2 = conn2.getresponse()
print('\nSkills List:', resp2.status)
print(resp2.read().decode()[:300])

# Step 3: Search skills
conn3 = http.client.HTTPConnection('127.0.0.1', 8081)
conn3.request('GET', '/api/openclaw/skills/search?q=web', headers={'Authorization': 'Bearer ' + token})
resp3 = conn3.getresponse()
print('\nSkills Search:', resp3.status)
print(resp3.read().decode()[:300])

# Step 4: Install skill
conn4 = http.client.HTTPConnection('127.0.0.1', 8081)
conn4.request('POST', '/api/openclaw/skills/install', 
    body=json.dumps({'skillName':'web-search'}),
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
resp4 = conn4.getresponse()
print('\nSkills Install:', resp4.status)
print(resp4.read().decode()[:300])

# Step 5: Remove skill
conn5 = http.client.HTTPConnection('127.0.0.1', 8081)
conn5.request('POST', '/api/openclaw/skills/remove', 
    body=json.dumps({'skillName':'web-search'}),
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
resp5 = conn5.getresponse()
print('\nSkills Remove:', resp5.status)
print(resp5.read().decode()[:300])

print('\n=== All API tests completed ===')
