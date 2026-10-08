// useIncidentMarkers (#233 B1.5-2)
//
// 把 /incidents/markers/timeline 返回的事件列表转成 ECharts markPoint/markLine
// 数据, 并按当前图表 xAxis 的刻度(小时数组 / 日期数组)对齐时间索引。
//
// 用法:
//   const { markers, loadMarkers, buildChartMarks, markerDetailMap } = useIncidentMarkers()
//   await loadMarkers({ start: '2026-10-08', end: '2026-10-08' })  // 今日窗口
//   // 趋势图 x 轴 labels = ['00:00', '01:00', ...]
//   const marks = buildChartMarks(labels, { kind: 'hourly' })
//   // 将 marks 注入到任一 series 的 markPoint/markLine 字段
//   series[0].markPoint = marks.markPoint
//   series[0].markLine = marks.markLine
//
// 设计说明（Design Rationale · #233 B1.5-2）:
//   - 同一组件复用于 Today (小时轴) 和 OperationDashboard (日轴): 通过 kind
//     参数切换时间→索引的映射逻辑。
//   - 点事件(异常事故 / start_at 空的经营事件)用 markPoint 在时间索引处
//     标一个气泡(value=标题缩写); 区间事件(start_at+end_at 都有)用 markLine
//     画一条竖线区间(startXAxis ~ endXAxis)。
//   - 点击 markPoint/markLine 弹事件详情: 详情数据已预存于 markerDetailMap,
//     用 params.data.id 查表即可。
//   - 不做 markPoint 重叠避让: 事件密度低(经营事件+异常事故一天通常 < 5 个),
//     echarts 自带的 markPoint 自动避让已足够。
//   - 颜色按 event_type 区分: 经营事件=success(绿), 异常事故=danger(红)。

import { ref } from 'vue'
import dayjs from 'dayjs'
import { listIncidentMarkers } from '@/api/incident'

export function useIncidentMarkers() {
  const markers = ref([])
  const markerDetailMap = ref({}) // { id: markerObj }
  const loading = ref(false)

  async function loadMarkers({ start, end, event_type } = {}) {
    if (!start || !end) return
    loading.value = true
    try {
      const res = await listIncidentMarkers({ start, end, event_type })
      const list = res.data || []
      markers.value = list
      const map = {}
      for (const m of list) map[m.id] = m
      markerDetailMap.value = map
    } catch (e) {
      markers.value = []
      markerDetailMap.value = {}
    } finally {
      loading.value = false
    }
  }

  // 时间 → xAxis 索引
  // kind=hourly: labels 形如 ['00:00', '01:00', ...]; 把 occurred_at/start_at 转 HH:mm → 找最近的 label
  // kind=daily:  labels 形如 ['10-01', '10-02', ...]; 把日期转 MM-DD → 找最近的 label
  function _timeToIdx(timeStr, labels, kind) {
    if (!timeStr || !labels?.length) return null
    const t = dayjs(timeStr)
    if (!t.isValid()) return null
    let key
    if (kind === 'hourly') {
      key = t.format('HH:mm')
      // 完全相等
      let idx = labels.indexOf(key)
      if (idx >= 0) return idx
      // 最近(按时间差最小)
      const tMin = t.hour() * 60 + t.minute()
      let bestIdx = -1
      let bestDelta = Infinity
      for (let i = 0; i < labels.length; i++) {
        const [h, m] = (labels[i] || '').split(':').map((x) => parseInt(x, 10) || 0)
        const labMin = h * 60 + m
        const delta = Math.abs(labMin - tMin)
        if (delta < bestDelta) { bestDelta = delta; bestIdx = i }
      }
      return bestIdx
    }
    if (kind === 'daily') {
      key = t.format('MM-DD')
      let idx = labels.indexOf(key)
      if (idx >= 0) return idx
      // 兼容 YYYY-MM-DD 形式
      for (let i = 0; i < labels.length; i++) {
        if (labels[i] && labels[i].endsWith(key)) return i
      }
      // 最近一天
      const t0 = t.startOf('day')
      let bestIdx = -1
      let bestDelta = Infinity
      for (let i = 0; i < labels.length; i++) {
        const lab = labels[i] || ''
        // labels 可能是 'MM-DD' 或 'YYYY-MM-DD' 或 'M-D'
        const parsed = dayjs(lab.length === 5 ? `${dayjs().year()}-${lab}` : lab)
        if (!parsed.isValid()) continue
        const delta = Math.abs(parsed.startOf('day').diff(t0, 'day'))
        if (delta < bestDelta) { bestDelta = delta; bestIdx = i }
      }
      return bestIdx
    }
    return null
  }

  // 构造 ECharts markPoint/markLine 数据
  // 返回: { markPoint: {...}, markLine: {...} }
  function buildChartMarks(labels, { kind = 'hourly' } = {}) {
    if (!markers.value.length) {
      return { markPoint: { data: [] }, markLine: { data: [] } }
    }
    const pointData = []  // markPoint.data
    const lineData = []   // markLine.data (每条是 [{coord}, {coord}])

    for (const m of markers.value) {
      const isRange = m.event_type === '经营事件' && m.start_at && m.end_at
      if (isRange) {
        // 区间事件 → markLine
        const sIdx = _timeToIdx(m.start_at, labels, kind)
        const eIdx = _timeToIdx(m.end_at, labels, kind)
        if (sIdx == null && eIdx == null) continue
        const s = sIdx != null ? sIdx : (eIdx != null ? eIdx : 0)
        const e = eIdx != null ? eIdx : s
        lineData.push([
          { coord: [Math.min(s, e)], name: m.title, id: m.id, value: m.title },
          { coord: [Math.max(s, e)], id: m.id },
        ])
      } else {
        // 点事件 → markPoint (occurred_at 优先, 经营事件降级用 start_at)
        const t = m.occurred_at || m.start_at
        if (!t) continue
        const idx = _timeToIdx(t, labels, kind)
        if (idx == null) continue
        pointData.push({
          name: m.title,
          coord: [idx, 0],  // y=0 让 markPoint 自动定位
          value: m.title,
          id: m.id,
          // 视觉区分: 经营事件绿, 异常事故红; severity 高时加边框
          itemStyle: {
            color: m.event_type === '经营事件' ? '#67c23a' : '#f56c6c',
            borderColor: m.severity === '高' ? '#303133' : 'rgba(0,0,0,0)',
            borderWidth: m.severity === '高' ? 2 : 0,
          },
          symbol: m.event_type === '经营事件' ? 'pin' : 'triangle',
          symbolSize: 38,
          label: {
            show: true,
            formatter: m.title.length > 4 ? m.title.slice(0, 4) + '…' : m.title,
            fontSize: 10,
            color: '#fff',
          },
        })
      }
    }

    return {
      markPoint: {
        symbol: 'pin',
        symbolSize: 38,
        data: pointData,
        // 标注不挡视线(默认 z=2, 数据线在上层)
        z: 3,
      },
      markLine: {
        // 用 vertical 竖线表示区间起止 + 中间画连接
        symbol: ['none', 'none'],
        lineStyle: { type: 'solid', color: '#67c23a', width: 2, opacity: 0.7 },
        label: { show: true, formatter: (p) => p.data?.name || '', fontSize: 10 },
        data: lineData,
        z: 3,
      },
    }
  }

  // 根据 chartClick params 找到对应事件详情
  function resolveClick(params) {
    if (!params) return null
    // markPoint: params.componentType === 'markPoint'; data 含 id
    // markLine:  params.componentType === 'markLine'; data.id (单端)
    const id = params?.data?.id
    if (id == null) return null
    return markerDetailMap.value[id] || null
  }

  return {
    markers,
    markerDetailMap,
    loading,
    loadMarkers,
    buildChartMarks,
    resolveClick,
  }
}
