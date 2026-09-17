// ---------------------------------------------------------------------------
// ПРОВЕРКА ОКРУЖЕНИЯ, а не приложение.
//
// Задача этого файла одна: доказать, что на машине собирается связка
// MSVC + Qt + CMake + Ninja, и что собираются ОБА режима — монолит и DLL.
// Восемнадцатого числа заменяется настоящим окном.
// ---------------------------------------------------------------------------
#include <QApplication>
#include <QLabel>
#include <QVBoxLayout>
#include <QWidget>

int main(int argc, char** argv)
{
    QApplication app(argc, argv);

    QWidget w;
    w.setWindowTitle(QStringLiteral("ВКД-Риск — проверка окружения"));
    w.resize(560, 220);

    auto* layout = new QVBoxLayout(&w);
    layout->addWidget(new QLabel(QStringLiteral(
        "Окружение собрано.\n\n"
        "Конфигурация сборки: %1\n"
        "Qt: %2")
        .arg(QStringLiteral(VKD_BUILD_CONFIG))
        .arg(QStringLiteral(QT_VERSION_STR))));

    w.show();
    return app.exec();
}
