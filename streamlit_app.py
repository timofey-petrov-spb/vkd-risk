# -*- coding: utf-8 -*-
"""Точка входа для Streamlit Community Cloud (и любого запуска не из корня репозитория).

Streamlit кладёт в sys.path каталог самого скрипта, а не корень проекта, поэтому
`streamlit run app/main.py` на площадке ломает импорты `app.*` и `vkd.*`. Здесь корень
добавляется в путь явно, рабочий каталог переводится в корень (относительные пути данных
и кеша), затем исполняется app/main.py как главный скрипт. Локально по-прежнему можно
запускать `python -m streamlit run app/main.py`.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)
runpy.run_path(os.path.join(ROOT, 'app', 'main.py'), run_name='__main__')
