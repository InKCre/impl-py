import { extensionDocs } from '../../../../../docs/_shared/website/extension-docs/preset.mts'

export default {
  ...extensionDocs({
    name: 'inkcre/twitter',
    title: 'Twitter Core 运行与配置',
    scope: 'python',
    description: '在 Core 上运行 Twitter 的协议接入和 Bookmark 采集，配置授权及运行参数。',
    sidebar: [{ text: '运行与配置', link: '/' }],
  }),
  lang: 'zh-CN',
}
