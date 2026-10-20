import { extensionDocs } from '../../../../../docs/_shared/website/extension-docs/preset.mts'

export default extensionDocs({
  name: 'inkcre/caldav', title: 'CalDAV for InKCre', scope: 'global',
  description: '用 Android 日历、Tasks.org 和联系人应用同步 InKCre。',
  sidebar: [{ text: '功能与范围', link: '/' }, { text: '连接 Android 手机', link: '/android' }],
})
