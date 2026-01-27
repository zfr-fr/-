# Unity 性能影响代码规范（结论版）

说明：
1) 当前环境无法直接访问外部链接，以下内容基于 Unity 常见性能最佳实践整理，并结合用户示例（如循环中避免 List.Remove）。
2) 如需与原文逐条对齐，请提供原文要点或截图，我可以继续补充与校验。

## 1. 统计范围
- 目标：总结“明显影响性能”的代码规范，强调 CPU、GC、渲染、物理、IO 的高频陷阱。
- 适用场景：移动端/中低端设备优先；PC/主机可酌情放宽，但热路径规则仍需遵守。

## 2. 规范统计（按影响类型归类）

### A. GC/内存分配（高优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| A1 | 避免在 Update/FixedUpdate/LateUpdate 中 new 对象 | 频繁 GC | 预分配、对象池 |
| A2 | 避免在热路径中使用 LINQ（Where/Select/ToList） | 分配+CPU | 改为 for/forEach 或手写循环 |
| A3 | 避免在循环中拼接字符串 | 频繁分配 | 使用 StringBuilder 或缓存格式化结果 |
| A4 | 避免在热路径频繁创建临时数组 | 触发 GC | 使用复用数组或 NonAlloc 版本 |
| A5 | 避免装箱/拆箱（如 object/非泛型集合） | GC+CPU | 使用泛型容器与泛型接口 |
| A6 | 避免频繁 new List/Dictionary | 分配+扩容 | 复用容器并 Clear，设置初始容量 |
| A7 | 避免频繁 new WaitForSeconds | 分配 | 缓存常用 WaitForSeconds 或改用计时器 |
| A8 | 避免频繁访问 renderer.material | 隐式实例化 | 读用 sharedMaterial，写用 MaterialPropertyBlock |

### B. 热路径 CPU（高优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| B1 | 禁止空 Update 或无意义 Update | CPU 浪费 | 无需更新时禁用脚本 |
| B2 | 避免 GameObject.Find/FindObjectOfType | 线性查找 | Awake/Start 缓存引用 |
| B3 | 缓存 transform、Camera.main 等引用 | 原生调用开销 | Awake 时缓存到字段 |
| B4 | 比较距离用 sqrMagnitude | 避免开方 | 仅比较平方距离 |
| B5 | 避免 Mathf.Pow 用于简单乘法 | 计算开销大 | 用 x * x 替代 |
| B6 | 避免 foreach 遍历接口/IEnumerable/Transform | 可能分配 | 热路径用 for/数组缓存 |
| B7 | 使用 CompareTag 而非 tag == "X" | 更快更安全 | CompareTag |
| B8 | 避免 SendMessage/BroadcastMessage/Invoke(string) | 反射开销 | 直接方法调用 |
| B9 | 避免频繁设置相同 Animator 参数 | CPU 浪费 | 比较变化后再设置 |

### C. 集合与算法（高优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| C1 | 循环中避免 List.Remove/RemoveAt | O(n) 移动 | 末尾交换移除或标记后统一移除 |
| C2 | 避免在大集合中频繁 Contains/IndexOf | 线性查询 | 使用 HashSet/Dictionary |
| C3 | 避免在循环中频繁插入到 List 中间 | O(n) 移动 | 追加后排序或用链表/缓存 |
| C4 | 明确设置 List/Dictionary 初始容量 | 频繁扩容 | new List<T>(capacity) |
| C5 | 避免每帧排序/全量重建集合 | CPU 峰值 | 增量更新或局部修正 |

### D. 渲染/材质（中高优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| D1 | 避免频繁访问 renderer.material | 产生实例+GC | 读用 sharedMaterial，写用 MaterialPropertyBlock |
| D2 | 避免每帧改动材质关键字与 RenderQueue | 打断批处理 | 批量修改或预先分组 |
| D3 | 避免每帧动态修改 Mesh 顶点/法线 | CPU+带宽 | 使用 Mesh.MarkDynamic 并减少更新频率 |
| D4 | 尽量减少每帧 SetActive 频繁开关 | 组件生命周期开销 | 合并切换时机 |

### E. 物理/动画/协程（中高优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| E1 | 避免 Physics.RaycastAll/OverlapSphere 频繁调用 | 分配+CPU | 使用 NonAlloc 版本并复用数组 |
| E2 | 避免在 OnTriggerStay/OnCollisionStay 做重逻辑 | 每物理帧触发 | 使用计时器/阈值判断 |
| E3 | 避免高频 StartCoroutine/StopCoroutine | 分配+调度 | 长驻协程或 Update 计时 |
| E4 | 避免每帧创建新的协程迭代器 | 分配 | 缓存迭代器或复用逻辑 |
| E5 | 仅在需要时开启 Rigidbody 物理 | 物理开销 | kinematic/休眠控制 |

### F. 资源加载/IO（中优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| F1 | 避免运行时 Resources.Load 大量资源 | 卡顿 | 预加载或 Addressables 异步 |
| F2 | 避免每帧 IO（文件/网络） | 阻塞 | 缓冲与批量提交 |
| F3 | 避免频繁 Instantiate/Destroy | CPU+GC+卡顿 | 对象池复用 |

### G. 日志/调试/诊断（中优先级）
| # | 规范 | 影响 | 推荐做法 |
|---|---|---|---|
| G1 | 避免 Debug.Log 在循环/热路径输出 | 分配+IO | 用条件编译或采样式日志 |
| G2 | 避免在发布版本开启深度 Profile | 影响帧率 | 仅在开发阶段使用 |

## 3. 关键示例（与需求一致的典型规则）
1) 循环中避免 List.Remove/RemoveAt  
   - 影响：每次删除都会移动后续元素，复杂度 O(n)，大量删除会引发卡顿。  
   - 替代：将要删除的元素与末尾元素交换，然后 RemoveAt(last)，或先标记、循环外统一删除。

2) 在 Update 中频繁 new / 字符串拼接  
   - 影响：持续产生 GC，导致帧率波动。  
   - 替代：复用对象、StringBuilder 或缓存格式化字符串。

3) 每帧 Find/GetComponent  
   - 影响：反复查找与原生调用开销。  
   - 替代：Awake/Start 缓存引用。

### 3.1 GC 优化要点与代码示例
GC 优化核心：**减少 C# 堆内存创建，控制高频分配**，尤其避免在 Update/LateUpdate/FixedUpdate 里出现 GC Alloc。

常见高频分配来源：
- new 对象/集合（List、Dictionary 等）
- LINQ（Where/Select/ToList 等）
- 字符串拼接/插值/格式化
- 装箱/拆箱与非泛型集合

**示例：Update 中的 GC Alloc（坏/好）**

坏例（每帧分配 List 与字符串）：
```csharp
using UnityEngine;
using UnityEngine.UI;
using System.Collections.Generic;

public class GcAllocBad : MonoBehaviour
{
    public Text label;
    public List<Transform> allTargets = new List<Transform>();

    void Update()
    {
        // GC Alloc: 每帧 new 集合
        var nearTargets = new List<Transform>();
        foreach (var t in allTargets)
        {
            var delta = t.position - transform.position;
            if (delta.sqrMagnitude < 25f)
            {
                nearTargets.Add(t);
            }
        }

        // GC Alloc: 每帧字符串拼接
        label.text = "Near: " + nearTargets.Count;
    }
}
```

好例（复用容器，仅在数据变化时更新字符串）：
```csharp
using UnityEngine;
using UnityEngine.UI;
using System.Collections.Generic;
using System.Text;

public class GcAllocBetter : MonoBehaviour
{
    public Text label;
    public List<Transform> allTargets = new List<Transform>();

    private readonly List<Transform> _nearTargets = new List<Transform>(64);
    private readonly StringBuilder _sb = new StringBuilder(32);
    private Transform _self;
    private int _lastCount = -1;

    void Awake()
    {
        _self = transform;
    }

    void Update()
    {
        _nearTargets.Clear();
        for (int i = 0; i < allTargets.Count; i++)
        {
            var t = allTargets[i];
            var delta = t.position - _self.position;
            if (delta.sqrMagnitude < 25f)
            {
                _nearTargets.Add(t);
            }
        }

        if (_nearTargets.Count != _lastCount)
        {
            _sb.Clear();
            _sb.Append("Near: ");
            _sb.Append(_nearTargets.Count);
            label.text = _sb.ToString();
            _lastCount = _nearTargets.Count;
        }
    }
}
```
说明：上例通过复用 List 与 StringBuilder，并在数据变化时才更新 UI，显著降低了 Update 中的 GC Alloc。

## 4. 结论
Unity 性能问题的主要来源是“热路径 CPU 过载”和“频繁 GC”。  
规范的核心结论是：**热路径不分配、少查找、少分支、少反射、少全局搜索**。  
在实际项目中，应优先治理 Update/FixedUpdate/渲染与物理相关的代码，再处理加载与日志等低频环节。  
若项目对性能要求高，建议配合 Profiler 建立基准场景并持续监控。
