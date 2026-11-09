# frontend：Vue3 仪表盘（状态看板 / 配置 / 历史）

## 入口
- `src/App.vue`：页面骨架与视图切换；`src/api.js`：后端 API 封装（所有请求经此，勿直接 fetch）
- `src/components/`：StatusHeader / TasksCard / PackagesCard / ConsumptionCard / ConfigView / TelegramCard / LoginCard / HistoryView
- `src/style.css`：设计令牌（间距、颜色、圆角）统一定义处

## 契约
- 后端默认托管 `dist/`，改动后必须 `npm run build` 才在容器内生效
- 布局约束：状态页展示任务进度、资源包、最低消费预估、手动运行；登录/连接等操作只在配置页，状态页只读展示

## 坑
- 间距与组件样式必须走 `style.css` 令牌，散落的硬编码 px 是历史回归来源
- 表单控件（尤其保存配置按钮）曾出现原生默认样式外框，新增控件时核对令牌化样式
