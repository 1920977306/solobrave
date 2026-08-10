import json

with open('data/agents.json', 'r') as f:
    data = json.load(f)

rule8 = '\n\n## 规则8：更新商品数据用PUT /api/products/:id\n当用户要求修改商品信息时，先搜索商品获取ID，再发PUT请求更新。\n搜索商品: curl -s "http://localhost:8081/api/products?q=商品名" -H "X-Agent-Id: emp_1780199176680"\n可更新字段：\n- name: 商品名称\n- brand: 品牌名\n- category: 分类\n- scene: 穿搭场景（字符串，如"日常休闲"）\n- price: 价格（数字，单位元）\n- original_price: 原价（数字，单位元）\n- commission_rates: 佣金比例（JSON对象，格式如{"自然流":20,"投放期":8}，自然流为自然流量佣金，投放期为投放期佣金）\n- sku_specs: SKU规格（JSON对象，如{"颜色":["白色","蓝色"],"尺码":["35-40"]}）\n- status: 状态（active或inactive）\n更新示例: curl -s -X PUT http://localhost:8081/api/products/商品ID -H "Content-Type: application/json" -H "X-Agent-Id: emp_1780199176680" -d \'{"name":"商品名","price":129,"original_price":259,"commission_rates":{"自然流":20,"投放期":8},"sku_specs":{"颜色":["白色"],"尺码":["35-40"]},"scene":"日常休闲"}\'\n注意：sku_specs是JSON对象，key是规格名称，value是选项数组。'

for a in data:
    if a.get('id') == 'emp_1780199176680':
        sp = a.get('systemPrompt', '')
        if '规则8' not in sp:
            a['systemPrompt'] = sp + rule8
            print('Added rule 8 to Helen systemPrompt')
        else:
            print('Rule 8 already exists, skipping')
        break

with open('data/agents.json', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('Done.')
