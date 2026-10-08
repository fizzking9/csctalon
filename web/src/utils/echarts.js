// -*- coding: utf-8 -*-
/**
 * ECharts 按需注册(S-P1-3 / B2-9)
 *
 * 设计说明(Design Rationale):
 *   原先 BaseChart.vue 写 `import * as echarts from 'echarts'` 引整包,
 *   地图/雷达/树图/仪表盘等全站从未使用的能力(~半数体积)全被打进 BaseChart chunk。
 *   本模块作为全站唯一入口, 只注册实际用到的 charts/components/renderer:
 *     charts  : bar / line / pie / heatmap
 *     components: title / tooltip / grid / legend / visualMap(热力图)
 *                 markPoint / markLine / markArea(事件标注 #233/#234)
 *     renderer: Canvas
 *
 * 取舍:
 *   · 不引 SVGRenderer —— 全站默认 canvas, 无导出 SVG 需求;
 *   · 新增图表类型时必须在此登记, 否则 setOption 静默不渲染(刻意收敛)。
 */

import { use, init } from 'echarts/core'
import { BarChart, LineChart, PieChart, HeatmapChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  VisualMapComponent,
  MarkPointComponent,
  MarkLineComponent,
  MarkAreaComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([
  CanvasRenderer,
  BarChart,
  LineChart,
  PieChart,
  HeatmapChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  VisualMapComponent,
  MarkPointComponent,
  MarkLineComponent,
  MarkAreaComponent,
])

export { init }
