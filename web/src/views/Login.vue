<template>
  <div class="login-container">
    <el-card class="login-card">
      <div class="login-header">
        <el-icon :size="40" color="#409eff"><Monitor /></el-icon>
        <h2>{{ siteInfo.site_name || '数据分析工具应用' }}</h2>
        <p class="sub">{{ siteInfo.site_subtitle || '专注数据分析与内部工具' }}</p>
      </div>
      <el-alert
        v-if="route.query.expired"
        title="登录已过期，请重新登录"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />
      <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleLogin">
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            size="large"
            placeholder="账号"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            size="large"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            :loading="loading"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <div class="login-footer">
        <p v-if="siteInfo.beian_note" class="site-note">{{ siteInfo.beian_note }}</p>
        <p class="site-info">
          <a v-if="siteInfo.icp_no" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">{{ siteInfo.icp_no }}</a>
          <a v-else href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">ICP备案号：待备案</a>
          <template v-if="siteInfo.psb_no">
            <span class="sep">|</span>
            <a href="https://beian.mps.gov.cn/" target="_blank" rel="noopener noreferrer">{{ siteInfo.psb_no }}</a>
          </template>
        </p>
        <p class="copyright">© {{ currentYear }} {{ siteInfo.copyright_holder || 'tanzk' }} · {{ siteInfo.site_name || '数据分析工具应用' }}</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { getSiteInfo } from '@/api/siteInfo'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const formRef = ref()
const loading = ref(false)

const currentYear = new Date().getFullYear()
const siteInfo = ref({})

async function loadSiteInfo() {
  try {
    const res = await getSiteInfo()
    siteInfo.value = res.data || {}
  } catch (e) {
    // 接口异常不影响登录, 使用内置默认值
    siteInfo.value = {}
  }
}
onMounted(loadSiteInfo)

const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      await authStore.login(form.username, form.password)
      ElMessage.success('登录成功')
      const redirect = route.query.redirect || '/dashboards/today'
      router.push(redirect)
    } catch (e) {
      // 错误已在拦截器处理
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped>
.login-container {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card {
  width: 400px;
  padding: 20px 10px;
}
.login-header {
  text-align: center;
  margin-bottom: 24px;
}
.login-header h2 {
  margin: 12px 0 4px;
  color: #303133;
}
.login-header .sub {
  color: #909399;
  font-size: 12px;
}
.tips {
  text-align: center;
}
.login-footer {
  margin-top: 20px;
  text-align: center;
  color: #909399;
  font-size: 12px;
  line-height: 1.8;
}
.login-footer .site-note {
  color: #b0b3b8;
}
.login-footer .sep {
  margin: 0 8px;
  color: #c0c4cc;
}
.login-footer a {
  color: #909399;
  text-decoration: none;
}
.login-footer a:hover {
  color: #409eff;
}
</style>
