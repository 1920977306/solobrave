# SoloBrave 部署指南

## 方式一：Vercel（推荐，30秒部署）

1. 访问 https://vercel.com/new
2. 点击 "Continue with GitHub" 登录
3. 选择 "Import Git Repository"
4. 粘贴你的 GitHub 仓库地址（如 `https://github.com/你的用户名/solobrave`）
5. Framework Preset 选 **"Other"**
6. 点击 **Deploy**
7. 30 秒后得到一个 URL，如 `https://solobrave.vercel.app`

## 方式二：GitHub Pages（免费，适合国内访问）

1. 把项目推送到 GitHub 仓库
2. 进入仓库 → Settings → Pages
3. Source 选择 "Deploy from a branch"
4. Branch 选 "main"，文件夹选 "/ (root)"
5. 点击 Save
6. 等待 1-2 分钟，访问 `https://你的用户名.github.io/solobrave`

## 方式三：Netlify Drop（无需注册）

1. 访问 https://app.netlify.com/drop
2. 把项目文件夹拖拽到页面上
3. 自动部署，得到一个随机 URL
4. 可以自定义域名

## 方式四：Cloudflare Pages

1. 访问 https://dash.cloudflare.com
2. 登录 → Pages → Create a project
3. 上传项目文件夹或连接 GitHub
4. 部署完成

## 方式五：本地运行（开发测试）

```bash
# 进入项目目录
cd solobrave

# Python
python -m http.server 8080

# Node.js
npx serve .

# 打开浏览器访问
http://localhost:8080
```

## 部署前检查清单

- [ ] `index.html` 存在且路径正确
- [ ] CSS 文件路径正确（`css/*.css`）
- [ ] JS 文件路径正确（`js/*.js`）
- [ ] 没有 404 错误
- [ ] 首次打开能正常显示引导弹窗

## 自定义域名

所有平台都支持自定义域名：
- Vercel: Project Settings → Domains
- GitHub Pages: Settings → Pages → Custom domain
- Netlify: Domain settings → Add custom domain
- Cloudflare: 自动 DNS 管理
