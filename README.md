[README.md](https://github.com/user-attachments/files/27720070/README.md)
# 📊 基金收益追踪系统

自动化追踪你在多个平台的基金收益情况，每天自动更新数据并生成可视化报告。

## ✨ 功能特性

- 📈 **实时数据**：每日自动抓取基金净值、收益率等数据
- 🎨 **可视化展示**：净值走势图、收益统计一目了然
- 🔄 **自动更新**：通过GitHub Actions每天自动运行
- 📱 **响应式设计**：支持手机、平板、电脑访问
- 🏦 **多平台支持**：支付宝、理财通、招商银行

## 📁 项目结构

```
fund-tracker/
├── fetch_fund_data.py      # 数据抓取脚本
├── index.html              # 可视化页面
├── requirements.txt        # Python依赖
├── .github/
│   └── workflows/
│       └── update.yml     # GitHub Actions配置
└── data/
    └── funds_data.json    # 生成的基金数据（自动生成）
```

## 🔍 数据来源

本系统使用[天天基金网](https://fund.eastmoney.com/)的公开API获取数据，合规合法。

## ⚠️ 注意事项

1. 基金收益计算基于历史净值，实际收益以各平台为准
2. 天天基金网的实时数据为估算值，仅供参考
3. 建议定期检查数据准确性

## 📄 许可证

MIT License

## 🙏 致谢

- [天天基金网](https://fund.eastmoney.com/) - 提供基金数据API
- [Chart.js](https://www.chartjs.org/) - 图表库
