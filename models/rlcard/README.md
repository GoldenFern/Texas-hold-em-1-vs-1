# RLCard 模型目录

这里放置 RLCard 的 DQN / NFSP / DMC checkpoint。模型文件不会入库（见根目录 `.gitignore`）。

## 先说明：官方没有 NLHE checkpoint

RLCard 官方 model zoo 只提供 Leduc Hold'em 的 CFR 模型和几个规则模型，**没有发布 no-limit-holdem 的 DQN / NFSP / DMC checkpoint**。网上能找到的 NLHE `.pth` 基本是第三方训练产物，质量和安全性都需要自己确认。

本目录有两类模型：

**项目规则模型（73 维 / 10 动作，推荐）**

| 文件 | 来源 | 说明 |
|---|---|---|
| `nlh_dqn_project.pth` | DQN 40k checkpoint，10k 手重选 | 当前默认 DQN。 |
| `nlh_dqn_project_selected.pth` | 同上 | 选中版本副本。 |
| `nlh_dqn_project_final.pth` | DQN 100k 最终 checkpoint | 保留的最终版，10k 手评估略低于 40k。 |
| `nlh_dqn_project_best.pth` | 训练中按 2k 评估选出的 60k | 旧的选择逻辑，保留对照。 |
| `nlh_nfsp_project.pth` | NFSP 70k checkpoint，10k 手重选 | 当前默认 NFSP。100k 训练因命令超时停在约 72k。 |
| `nlh_nfsp_project_selected.pth` | 同上 | 选中版本副本。 |
| `nlh_nfsp_project_best.pth` | 训练中按 2k 评估选出的 50k | 旧的选择逻辑，保留对照。 |

**RLCard 原生规则模型（54 维 / 5 动作，仅作基线）**

| 文件 | 来源 | 说明 |
|---|---|---|
| `nlh_dqn_thirdparty.pth` | GitHub `NGeorggin/COMPSCI175FinalProject` 的 `experiments/no_limit_holdem_dqn_result/model.pth` | 已去掉 replay memory，只保留策略网络。原作者是课程项目，未声明许可证；在本地引擎里对 RandomAgent 表现较弱，只适合做管线验证。 |
| `nlh_dqn.pth` | 本机 `scripts/train_rlcard.py --episodes 50000 --chips 1000 --reward-scale 0.01` | RLCard 双人自对弈 50k 手，128x128 MLP。 |
| `nlh_nfsp.pth` | 本机 `scripts/train_nfsp.py --episodes 20000 --chips 1000 --reward-scale 0.01` | RLCard 双人自对弈 20k 手，128x128 平均策略网络。 |

RLCard 原生规则模型来自 `no-limit-holdem` 环境。该环境的翻后行动顺序与本项目引擎不同：RLCard 在双人局让按钮/小盲先行动，本项目按标准单挑规则让大盲先行动。因此这些 checkpoint 直接放进本项目会有位置和行动顺序上的偏差，不能把胜率直接当作 Bot 的真实强弱。项目规则模型没有这个问题。

## 训练项目规则 DQN

```bash
python scripts/train_project_bot.py --algorithm dqn --hands 100000 --opponent mix
```

默认输出 `models/rlcard/nlh_dqn_project.pth`，验证集最好的版本保存为 `nlh_dqn_project_best.pth`。

## 训练项目规则 NFSP

```bash
python scripts/train_project_bot.py --algorithm nfsp --hands 100000 --opponent mix
```

默认输出 `models/rlcard/nlh_nfsp_project.pth`，验证集最好的版本保存为 `nlh_nfsp_project_best.pth`。

## 训练 RLCard 原生 DQN / NFSP

```bash
python scripts/train_rlcard.py --episodes 50000 --chips 1000 --reward-scale 0.01
python scripts/train_nfsp.py --episodes 50000 --chips 1000 --reward-scale 0.01
```

## DMC

RLCard 的 DMC 训练器使用 actor/learner 多进程，默认 `total_frames` 很大，训练成本明显高于 DQN/NFSP。目前没有可直接下载的可信 NLHE DMC checkpoint。

## 在 UI 中使用

1. 配置对局 → 座位选择 **RLCard**。
2. 使用项目规则模型时选 **Project DQN** 或 **Project NFSP**；旧的 RLCard 模型选 **DQN** / **NFSP** / **DMC**。
3. 在「模型文件」下拉框选择对应的 `.pth`。
4. 开始对局。

## 安全说明

- 项目规则模型是 `project-bot-v1` 格式，只包含 `state_dict` 和基础元数据，`ProjectBot` 使用 `torch.load(..., weights_only=True)` 加载。
- 旧的 RLCard checkpoint 是完整 Python 对象，`RLCardBot` 仍然使用 `weights_only=False`；只加载你自己训练或信任来源的文件。
- 第三方模型下载后建议先检查 zip 成员和 `data.pkl` 里的 GLOBAL 引用，再决定是否加载。
