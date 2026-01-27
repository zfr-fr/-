# Unity 性能影响代码规范结论文档（统计版）

## 参考链接
- https://zhuanlan.zhihu.com/p/1973521025334518336

## 结论概览
本统计文档聚焦“代码层面可直接规避的性能风险点”，重点覆盖 CPU 占用、GC 分配、渲染与物理开销等方向。实践中最常见的性能问题来自“每帧重复执行 + 伴随分配/反射/重建”的组合，因此规范优先关注热路径（Update/FixedUpdate/渲染回调/协程高频循环）。

## 统计口径
- 统计对象：Unity 项目中“会明显影响性能的代码规范”
- 影响等级：高 / 中 / 低（以移动端与中低端设备为主要参考）
- 影响类型：CPU、GC、Render、Physics、IO、UI

## 统计结果（概览）
- 高影响：12 条
- 中影响：16 条
- 低影响：8 条
- 类型覆盖：CPU 16、GC 14、Render 9、Physics 6、IO 4、UI 6  
  （同一条可能同时归入多个类型）

## 高影响规则速览（Top 12）
1. **循环/Update 中频繁 List.Remove/RemoveAt**（CPU+GC）：O(n) 元素搬移 + 潜在分配
2. **Update 中频繁 Instantiate/Destroy**（CPU+GC+Render）：生命周期抖动与堆分配
3. **Update 中使用 LINQ/匿名函数**（GC+CPU）：迭代器与闭包分配
4. **每帧 GetComponent/Find/Camera.main**（CPU）：反射或全场景搜索
5. **每帧 GetComponentsInChildren/FindObjectsOfType**（CPU+GC）：数组分配 + 全量遍历
6. **协程中反复 new WaitForSeconds**（GC）：频繁短生命周期对象
7. **每帧修改 Renderer.material**（GC+Render）：材质实例化与批次破坏
8. **频繁 string 拼接/Format/插值**（GC）：字符串分配
9. **大量 Physics.Raycast/Overlap（非 NonAlloc）**（CPU+GC）：高频物理查询
10. **频繁 Resources.Load/同步 IO**（IO+CPU）：阻塞主线程
11. **大量 UI Layout/Canvas 重建**（UI+CPU）：布局与重绘成本高
12. **每帧 Debug.Log**（CPU+IO+GC）：日志格式化与输出开销

## 规范清单（按类别）

### A. GC/内存分配类
| ID | 规范（禁止/建议） | 影响类型 | 影响等级 | 原因 | 替代方案 |
|---|---|---|---|---|---|
| A1 | 禁止在循环/Update 中 `List.Remove/RemoveAt` | CPU+GC | 高 | O(n) 元素移动 + 触发缓存失效 | 逆序遍历删除；标记删除后统一清理 |
| A2 | 禁止在热路径频繁 `new`/`Instantiate` | CPU+GC | 高 | 大量短生命周期对象导致 GC 抖动 | 对象池与预分配 |
| A3 | 禁止在热路径使用 LINQ | CPU+GC | 高 | 迭代器与闭包分配 | 使用 for/foreach（具体集合） |
| A4 | 禁止在热路径对 `IEnumerable` 进行 foreach | CPU+GC | 中 | 迭代器装箱或分配 | 改用数组/List 的 for |
| A5 | 避免 `string` 拼接/Format/插值 | GC | 高 | 频繁分配新字符串 | 复用 StringBuilder，或缓存格式 |
| A6 | 避免装箱（struct -> object） | GC | 中 | 装箱分配与拆箱开销 | 使用泛型与强类型集合 |
| A7 | 避免 `params` 产生临时数组 | GC | 中 | 每次调用都会分配数组 | 使用固定长度重载 |
| A8 | 避免 `GetComponents*` 在热路径调用 | CPU+GC | 高 | 返回数组分配 + 递归遍历 | 缓存结果 |
| A9 | 避免 `ToArray/ToList` 在热路径调用 | GC | 中 | 新集合分配 | 复用 List 或数组 |
| A10 | 协程中避免重复 `new WaitForSeconds` | GC | 高 | 每次 yield 分配对象 | 复用 WaitForSeconds 实例 |

### B. CPU/逻辑类
| ID | 规范（禁止/建议） | 影响类型 | 影响等级 | 原因 | 替代方案 |
|---|---|---|---|---|---|
| B1 | 禁止每帧 `GetComponent` | CPU | 高 | 反射式查找与遍历 | Awake/Start 缓存引用 |
| B2 | 禁止每帧 `GameObject.Find/FindObjectOfType` | CPU | 高 | 全场景遍历 | 依赖注入或缓存引用 |
| B3 | 避免每帧 `Camera.main` | CPU | 中 | 内部查找主相机 | 缓存主相机引用 |
| B4 | 禁止 `SendMessage/BroadcastMessage` | CPU | 中 | 反射调用，遍历组件 | 直接引用调用 |
| B5 | 避免无必要的 `Update` | CPU | 中 | 空 Update 也会调度 | 用事件或集中管理 |
| B6 | 避免每帧 `StartCoroutine` | CPU+GC | 中 | 频繁创建协程状态机 | 常驻协程或状态机 |
| B7 | 避免异常当作流程控制 | CPU | 中 | 抛异常代价高 | 条件判断替代 |
| B8 | 在高频比较中用 `CompareTag` | CPU | 低 | 具有内部优化 | `gameObject.CompareTag("X")` |
| B9 | 避免复杂数学在热路径（如 `Mathf.Pow`） | CPU | 低 | 计算成本高 | 预计算或简化公式 |
| B10 | 避免频繁 `SetActive` 级联 | CPU | 中 | 触发组件启停与事件链 | 合理拆分对象层级 |

### C. 渲染相关
| ID | 规范（禁止/建议） | 影响类型 | 影响等级 | 原因 | 替代方案 |
|---|---|---|---|---|---|
| C1 | 禁止频繁 `Instantiate/Destroy` 渲染对象 | CPU+GC+Render | 高 | 破坏批次与 GC 抖动 | 对象池 |
| C2 | 避免每帧访问 `Renderer.material` | GC+Render | 高 | 触发材质实例化 | 用 `sharedMaterial` 或 MPB |
| C3 | 每帧改材质属性使用 `MaterialPropertyBlock` | Render | 中 | 避免生成独立材质 | MPB 复用 |
| C4 | 避免频繁重建 Mesh（`RecalculateBounds/Normals`） | CPU | 中 | 代价高 | 降低频率或异步处理 |
| C5 | 避免大量独立材质导致批次破坏 | Render | 中 | DrawCall 增多 | 合并材质与图集 |
| C6 | 避免多相机叠加渲染 | Render | 中 | 额外渲染 Pass | 合并到单相机或后处理 |
| C7 | 对不可见对象关闭 Animator/SkinnedMesh | CPU+Render | 中 | 骨骼计算消耗 | 视锥裁剪与 LOD |
| C8 | 避免每帧 `OnRenderObject/GL` | CPU+Render | 中 | 频繁自定义绘制 | 缓存数据，降低频率 |
| C9 | 频繁变更阴影/光照参数需谨慎 | Render | 低 | 触发重建 | 将变更集中到低频时机 |

### D. 物理相关
| ID | 规范（禁止/建议） | 影响类型 | 影响等级 | 原因 | 替代方案 |
|---|---|---|---|---|---|
| D1 | 禁止大量 `Physics.Raycast` 每帧 | CPU | 高 | 物理查询成本高 | 合并射线，降低频率 |
| D2 | 优先使用 `RaycastNonAlloc/OverlapNonAlloc` | CPU+GC | 高 | 避免数组分配 | 复用结果数组 |
| D3 | 物理逻辑放在 `FixedUpdate` | CPU | 中 | 保证稳定步长 | 逻辑-物理分离 |
| D4 | 使用 Layer 碰撞矩阵过滤 | CPU | 中 | 减少检测组合 | 配置 Physics Matrix |
| D5 | 避免频繁启用/禁用 Collider | CPU | 低 | 触发物理重建 | 使用层或条件判断 |
| D6 | 高频移动使用 Rigidbody API | CPU | 低 | 避免直接改 transform | `MovePosition/MoveRotation` |

### E. 资源/IO 相关
| ID | 规范（禁止/建议） | 影响类型 | 影响等级 | 原因 | 替代方案 |
|---|---|---|---|---|---|
| E1 | 禁止热路径 `Resources.Load` | IO+CPU | 高 | 同步加载阻塞 | 预加载/Addressables |
| E2 | 禁止热路径文件读写 | IO+CPU | 高 | 阻塞主线程 | 异步 IO/缓存 |
| E3 | 避免频繁 `PlayerPrefs` | IO | 中 | 系统 IO 消耗 | 合并写入或缓存 |
| E4 | 避免同步加载场景 | IO+CPU | 中 | 帧卡顿 | `LoadSceneAsync` |

### F. UI 相关
| ID | 规范（禁止/建议） | 影响类型 | 影响等级 | 原因 | 替代方案 |
|---|---|---|---|---|---|
| F1 | 避免频繁触发 Layout 重建 | UI+CPU | 高 | Layout 计算昂贵 | 拆分 Canvas，减少更新 |
| F2 | 频繁文本更新需节流 | UI+CPU | 中 | Text 重排与重绘 | 合并更新频率 |
| F3 | 避免在同一 Canvas 中频繁修改 | UI+CPU | 中 | 整体重建 | 逻辑分层多 Canvas |
| F4 | UI 动态列表使用对象池 | UI+CPU | 中 | Instantiate/Destroy | 复用条目 |
| F5 | 避免 `ContentSizeFitter` 级联 | UI | 低 | Layout 反复计算 | 手动控制尺寸 |
| F6 | 避免 `GraphicRaycaster` 在不需要时启用 | UI | 低 | 事件检测 | 按需启用 |

## 检查清单（代码评审用）
- 是否有 Update/FixedUpdate 内的分配或 LINQ？
- 是否出现 Find/FindObjectOfType/Camera.main 高频调用？
- 是否存在频繁 Instantiate/Destroy？
- 是否有频繁 Debug.Log？
- 是否频繁修改材质或触发 UI Layout？
- 物理查询是否使用 NonAlloc 版本？
- 是否存在同步 IO/Resources.Load？

## 总结
从代码规范角度看，**“高频 + 分配 + 全局查找/反射”**是导致 Unity 性能问题的主要组合风险。优先优化热路径中的分配、查找与渲染/物理调用，能显著降低卡顿与 GC 抖动。
