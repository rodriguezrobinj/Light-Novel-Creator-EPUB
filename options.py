"""Ventana «Opciones»: todos los ajustes en un sitio, como las Preferencias de Acrobat.
La tabla OPTIONS declara cada opción una vez; páginas, buscador y ayudas salen de ella."""
import unicodedata
from collections import namedtuple

BOOK, APP = "El libro", "La aplicación"
CATEGORIES = {BOOK: ("Perfil y formato", "Aspecto", "Texto", "Ilustraciones", "PDF"),
              APP: ("Apariencia", "Vista previa", "Traducción", "Carpeta vigilada", "Informe de calidad")}
Option = namedtuple("Option", "category key kind label help")
OPTIONS = (
    Option("Perfil y formato", "profile", "choice", "Perfil",
           "Fija las opciones que importan para ese lector: gris, tamaño de imagen, formato Kobo…"),
    Option("Perfil y formato", "epub2", "check", "EPUB 2 (lectores antiguos)", "Para lectores de hace años que no abren EPUB 3."),
    Option("Perfil y formato", "kepub", "check", "Formato Kobo (KEPUB)",
           "El Kobo muestra páginas y estadísticas de lectura; el archivo acaba en .kepub.epub."),
    Option("Perfil y formato", "lang", "language", "Idioma del libro",
           "Decide cómo se parten las palabras, la voz de lectura y los avisos del informe."),
    Option("Aspecto", "style", "choice", "Estilo", "Letra, párrafos e inicio de capítulo del EPUB."),
    Option("Aspecto", "separator", "choice", "Separador de escena", "Cómo se ven los cambios de escena."),
    Option("Aspecto", "font", "font", "Letra", "Tipografía incrustada en el EPUB; «Del lector» usa la del aparato."),
    Option("Texto", "typography", "check", "Limpieza tipográfica",
           "Comillas «», puntos suspensivos, raya de diálogo y espacios, al estilo RAE."),
    Option("Texto", "hyphenate", "check", "Partir palabras con guiones (Kindle)",
           "El Kindle apenas parte palabras en español: sin esto el texto justificado queda con huecos."),
    Option("Texto", "reading", "check", "Minutos de lectura en el índice",
           "Cuánto se tarda en leer cada capítulo, en la página «Contenido»."),
    Option("Texto", "credits", "check", "Página de créditos",
           "Tras la portada, una página con los datos del libro: traductor, grupo, ilustrador…"),
    Option("Texto", "autocase", "check", "Corregir las mayúsculas al abrir si vienen en MAYÚSCULAS",
           "Los títulos escritos todo en mayúsculas pasan a mayúsculas al estilo español."),
    Option("Ilustraciones", "shrink", "check", "Reducir imágenes", "Las imágenes son casi todo el peso del EPUB."),
    Option("Ilustraciones", "shrink_size", "number", "Lado mayor, en píxeles", "Tamaño de las imágenes reducidas."),
    Option("Ilustraciones", "gray", "check", "Imágenes en gris", "Para e-readers de tinta: pesa menos y se ve igual."),
    Option("Ilustraciones", "trim", "check", "Recortar márgenes blancos de las ilustraciones",
           "Quita el borde blanco que traen algunas ilustraciones escaneadas."),
    Option("PDF", "whole_words", "check", "No partir palabras al final de línea",
           "La palabra que no cabe pasa entera a la línea siguiente, sin guion."),
    Option("PDF", "pdf_size", "choice", "Tamaño preferido", "Sale primero en el menú «PDF ▾»."),
    Option("Apariencia", "theme", "choice", "Tema", "«Sistema» sigue el modo claro u oscuro de Windows."),
    Option("Vista previa", "device", "choice", "Dispositivo al abrir", "Tamaño de página de la vista previa del EPUB."),
    Option("Vista previa", "dark", "check", "Modo oscuro al abrir", "Las vistas previas en claro sobre negro."),
    Option("Traducción", "translate", "translate", "Motor, modelo y clave de API",
           "Servicio que traduce, su modelo y su clave, e idiomas con los que empieza «Traducir»."),
    Option("Carpeta vigilada", "watch", "folder", "Carpeta vigilada",
           "Mientras la ventana esté abierta, cada PDF nuevo que llegue a ella se convierte solo."),
    Option("Informe de calidad", "echoes", "check", "Buscar ecos (palabras repetidas cerca)",
           "Muchos avisos de estilo: solo si se piden."),
)
# Lo que se guarda por libro y en «Libros nuevos»; la ruta de la letra propia va con su nombre
BOOK_KEYS = tuple(o.key for o in OPTIONS if o.category in CATEGORIES[BOOK]) + ("font_file",)


def fold(text):
    """Sin tildes ni mayúsculas: «guion» encuentra «guiones» y «Guión»."""
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def find(text):
    """Opciones cuyo nombre o ayuda contiene text."""
    needle = fold(text).strip()
    return [o for o in OPTIONS if needle and (needle in fold(o.label) or needle in fold(o.help))]
