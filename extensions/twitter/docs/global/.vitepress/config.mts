import { extensionDocs } from '../../../../../docs/_shared/website/extension-docs/preset.mts'

export default {
  ...extensionDocs({
    name: 'inkcre/twitter',
    title: 'Twitter / X 扩展',
    scope: 'global',
    description: '通过 x-monkey 保存帖子，或采集自己的 X 书签，并在 InKCre 中检索和阅读。',
    sidebar: [
      { text: '扩展概览', link: '/' },
      { text: '用 x-monkey 保存帖子', link: '/x-monkey' },
      { text: '授权与采集 Bookmark', link: '/bookmarks' },
      { text: '检索、阅读与媒体', link: '/reading' },
    ],
  }),
  lang: 'zh-CN',
}
