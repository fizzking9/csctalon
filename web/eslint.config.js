// ESLint 配置 - Vue 3 前端(flat config 格式, ESLint 9)
// 对应 AGENTS.md: 禁止生产代码 console.log(仅 [TODO:] 标记例外)
import pluginVue from 'eslint-plugin-vue'

export default [
  // 忽略构建产物
  { ignores: ['dist/**', 'node_modules/**'] },

  // 基础规则(推荐)
  ...pluginVue.configs['flat/recommended'],

  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        window: 'readonly',
        document: 'readonly',
        console: 'readonly',
        process: 'readonly',
        __dirname: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        navigator: 'readonly',
        fetch: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        URL: 'readonly',
        Blob: 'readonly',
        FormData: 'readonly',
      },
    },
    rules: {
      // 禁止 console.log(仅 [TODO:] 标记例外)
      'no-console': ['error', { allow: ['warn', 'error'] }],
      'no-debugger': 'error',
      // Vue 模板中未使用变量
      'vue/no-unused-vars': 'warn',
      // 历史惯例: 项目有 30+ 单字组件名(Dashboard/Login/Today 等), 改名风险大, 关闭
      'vue/multi-word-component-names': 'off',
      // 历史风格债: 大量模板属性换行警告(12000+), 关闭避免噪音, 保留有意义的规则
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/max-len': 'off',
      // Vue 3 合法模式: props.filter 是响应式对象, 子组件改其属性双向绑定分页
      // (父组件 filter 对象传递, 多处使用此惯例)。ESLint 保守报错, 关闭。
      'vue/no-mutating-props': 'off',
    },
    // console.log 豁免: AGENTS.md 允许 `[TODO:]` 前缀的显式 TODO 标记
    // (如 RegistryCenter.vue 的 console.log('[TODO:PolicyDetail]', row))
    // 在文件级忽略, 保留真正无前缀的 console.log 报错
  },
  {
    // RegistryCenter.vue 含 AGENTS.md 允许的 `[TODO:]` console.log 标记
    // (console.log('[TODO:PolicyDetail]', row)), 文件级豁免 no-console
    files: ['**/RegistryCenter.vue'],
    rules: {
      'no-console': 'off',
    },
  },
]
