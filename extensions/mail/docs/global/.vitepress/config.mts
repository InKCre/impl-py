import { extensionDocs } from '../../../../../docs/_shared/website/extension-docs/preset.mts'

export default {
  ...extensionDocs({
    name: 'inkcre/mail',
    title: 'Mail for InKCre',
    scope: 'global',
    description: '搜索、阅读和回看通过 Mail 采集的 newsletter。',
    sidebar: [{ text: 'Newsletter 阅读', link: '/' }],
  }),
  lang: 'zh-CN',
}
