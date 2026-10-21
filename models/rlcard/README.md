# RLCard 模型目录

这里放置 RLCard 的 DQN / NFSP / DMC checkpoint。模型文件不会入库（见根目录 `.gitignore`）。

## 训练一个 DQN

先安装 PyTorch：

```bash
pip install -r requirements-optional.txt
```

然后：

```bash
python scripts/train_rlcard.py --episodes 50000 --chips 1000 --reward-scale 0.01
```

默认输出 `models/rlcard/nlh_dqn.pth`。训练脚本会把 replay memory 清空后再保存，因此服务端只加载策略网络，文件通常只有几百 KB。

## 在 UI 中使用

1. 配置对局 → 座位选择 **RLCard**。
2. Agent 选择 **DQN**。
3. 在「模型文件」下拉框选择 `models/rlcard/nlh_dqn.pth`。
4. 开始对局。

## 说明

- RLCard 的 `no-limit-holdem` 是 1/2 盲注、2 人环境。我们的适配器按 RLCard 的 54 维 observation 与 5 个动作 ID 编码，确保训练与推理一致。
- DQN 在短训练下可能退化为频繁全下。建议至少训练 50k–100k 局，并尝试 `--reward-scale 0.01` 或更小的学习率。
- 模型文件是 PyTorch pickle，只加载你自己训练或信任来源的文件。
