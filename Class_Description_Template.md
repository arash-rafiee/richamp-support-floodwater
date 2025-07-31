# 🧱 Solid Python Class Documentation Format

This template helps you clearly describe the purpose, structure, and usage of Python classes, especially in scientific or modeling codebases like `Reader.py`.

---

## 📘 Class: `ClassName`

### 1. Purpose
A concise paragraph explaining **what the class does** and **why it exists**. Focus on its role in the bigger workflow.

---

### 2. Inputs (Constructor Arguments)

| Parameter Name           | Type     | Description                                                               |
|--------------------------|----------|---------------------------------------------------------------------------|
| `param1`                 | `type`   | What it represents and where it's typically used                          |
| `param2`                 | `type`   | Whether it's optional, a file path, an object, etc.                       |

---

### 3. Attributes

(Optional – only if different from inputs, or worth highlighting)

| Attribute Name           | Description                                                                 |
|--------------------------|-----------------------------------------------------------------------------|
| `self.attr1`             | How it's used/stored or derived from constructor                           |
| `self.reader`            | Instance of another class (if relevant), used for delegation or processing |

---

### 4. Key Methods

#### 🔹 `method_name(...)`
- **Purpose:** What the method does
- **Inputs:** List of arguments (if any) and what they are
- **Output:** What is returned (if anything)
- **Side Effects:** Any files written or logs printed

(Repeat for each method)

---

### 5. Workflow / Example Use

```python
reader = ClassName(arg1, arg2)
result = reader.method_name()
```

---

This format can be reused across your project to document complex components like wind readers, wave readers, elevation extraction tools, and more.
