cmake_minimum_required(VERSION 3.25)  # формат пресетов v6 требует 3.25+
project(VKDRisk LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# ---------------------------------------------------------------------------
# ДВА РЕЖИМА СБОРКИ. Оба обязаны собираться ВСЕГДА (CONTRACT.md, раздел 3).
#
#   VKD_MONOLITH=ON   каждый модуль STATIC и вшивается через /WHOLEARCHIVE.
#                     Режим разработки: быстрее и модуль не может молча выпасть.
#   VKD_MONOLITH=OFF  модули SHARED, грузятся из modules/ рядом с программой.
#                     Режим показа и поставки.
#
# Смысл двойного режима: если загрузка DLL сломается ночью перед защитой,
# переключается флаг и приложение остаётся рабочим.
# ---------------------------------------------------------------------------
option(VKD_MONOLITH   "Вшить модули в приложение статикой" OFF)
option(VKD_BUILD_TESTS "Собирать тесты" ON)
option(VKD_WITH_OCCT  "Геометрия и STEP через OpenCASCADE" OFF)

if(VKD_MONOLITH AND NOT MSVC)
    message(FATAL_ERROR "VKD_MONOLITH реализован через /WHOLEARCHIVE (MSVC).")
endif()

if(VKD_MONOLITH)
    set(VKD_LIBTYPE STATIC)
    add_compile_definitions(VKD_MONOLITH)
    set(VKD_BUILD_CONFIG "MONOLITH")
else()
    set(VKD_LIBTYPE SHARED)
    set(VKD_BUILD_CONFIG "MODULES")
endif()
message(STATUS "VKDRisk: конфигурация сборки — ${VKD_BUILD_CONFIG}")

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Widgets)

# ---------------------------------------------------------------------------
# ПРОВЕРКА ОКРУЖЕНИЯ. Пустое окно, ничего не считает.
# Задача одна: доказать, что MSVC + Qt + CMake + Ninja собираются в ОБОИХ
# режимах на ОБЕИХ машинах. Заменяется настоящим приложением 18.09.
# ---------------------------------------------------------------------------
add_executable(VKDRisk src/main.cpp)
target_link_libraries(VKDRisk PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets)
target_include_directories(VKDRisk PRIVATE ${CMAKE_SOURCE_DIR}/include)
target_compile_definitions(VKDRisk PRIVATE VKD_BUILD_CONFIG="${VKD_BUILD_CONFIG}")

if(MSVC)
    target_compile_options(VKDRisk PRIVATE /W4 /utf-8)
endif()

if(VKD_BUILD_TESTS)
    enable_testing()
endif()
