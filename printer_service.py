from pathlib import Path
import sqlite3
import time
import unicodedata

try:
    from escpos.printer import Usb  # Ou: from escpos.printer import Network
except ImportError:
    Usb = None

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lanchonete.db"

# =====================================================================
# CONFIGURAÇÃO DA IMPRESSORA TÉRMICA (AJUSTE AQUI)
# =====================================================================
# Exemplo USB: substitua pelos VID e PID reais da sua impressora (em hexadecimal)
USB_VENDOR_ID = 0x0416
USB_PRODUCT_ID = 0x5011
USB_OUT_EP = 3


def _strip_accents(value) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return normalized.encode("ascii", "ignore").decode("ascii")


def get_printer():
    if Usb is None:
        print("[Impressora] Biblioteca 'python-escpos' não instalada. Modo simulação ativo.")
        return None
    try:
        printer = Usb(idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID, out_ep=USB_OUT_EP)
        return printer
    except Exception as e:
        print(f"[Impressora] Aviso: Impressora USB não conectada ({e}). Modo simulação ativo.")
        return None


def _close_printer_safely(printer) -> None:
    if printer is None:
        return
    try:
        close_fn = getattr(printer, "close", None)
        if callable(close_fn):
            close_fn()
    except Exception:
        pass


def print_ticket(order: dict, printer=None) -> bool:
    if not isinstance(order, dict):
        order = dict(order or {})

    own_printer = printer is None
    if own_printer:
        printer = get_printer()

    order_id = order.get("id", "?")
    status = _strip_accents(order.get("status") or "Confirmado")
    client_name = _strip_accents(order.get("client_name") or "Cliente")
    client_phone = _strip_accents(order.get("client_phone") or "")
    delivery_type = _strip_accents(order.get("delivery_type") or "")
    address = _strip_accents(order.get("address") or "")
    payment = _strip_accents(order.get("payment") or "")
    items = _strip_accents(order.get("items") or "")
    total_str = f"{float(order.get('total') or 0.0):.2f}".replace(".", ",")

    try:
        if printer:
            try:
                printer.set(align="center", bold=True, width=1, height=1)
                printer.text("SUA LANCHONETE\n")
                printer.set(align="center", normal_text=True)
                printer.text("--------------------------------\n")
                printer.set(align="left", bold=True)
                printer.text(f"PEDIDO #{order_id} - {status}\n")
                printer.set(align="left", normal_text=True)
                printer.text(f"Cliente: {client_name}\n")
                if client_phone:
                    printer.text(f"Tel: {client_phone}\n")
                printer.text(f"Tipo: {delivery_type}\n")
                if address:
                    printer.text(f"Endereco: {address}\n")
                printer.text(f"Pagamento: {payment}\n")
                printer.text("--------------------------------\n")
                printer.text("ITENS:\n")
                printer.text(f"{items}\n")
                printer.text("--------------------------------\n")
                printer.set(align="center", bold=True)
                printer.text(f"TOTAL: R$ {total_str}\n\n\n")
                printer.cut()
                print(f"[Impressora] Pedido #{order_id} impresso com sucesso na impressora térmica!")
                return True
            except Exception as err:
                print(f"[Impressora] Falha física ao imprimir pedido #{order_id}: {err}")

        # Modo simulação no terminal caso a impressora não esteja conectada
        print("\n================ [COMANDA SIMULADA] ================")
        print(f"PEDIDO #{order_id} | STATUS: {status}")
        print(f"Cliente: {client_name} ({client_phone})")
        print(f"Tipo: {delivery_type} | Pagamento: {payment}")
        if address:
            print(f"Endereço: {address}")
        print("----------------------------------------------------")
        print(items)
        print("----------------------------------------------------")
        print(f"TOTAL: R$ {total_str}")
        print("====================================================\n")
        return True
    finally:
        if own_printer:
            _close_printer_safely(printer)


def print_order_now(order: dict) -> bool:
    """Chamada diretamente pelo main.py quando o pedido é Confirmado ou ao clicar em Imprimir."""
    try:
        return print_ticket(order)
    except Exception as err:
        print(f"[Impressora] Erro inesperado em print_order_now: {err}")
        return False


def check_and_print_orders():
    print(f"Monitor de impressão iniciado. Lendo banco em: {DB_PATH}")
    while True:
        conn = None
        try:
            if DB_PATH.exists():
                conn = sqlite3.connect(DB_PATH, timeout=10)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Busca apenas pedidos CONFIRMADOS que ainda não foram impressos
                cursor.execute(
                    "SELECT * FROM orders WHERE status = 'Confirmado' AND printed = 0 ORDER BY id ASC"
                )
                confirmed_orders = cursor.fetchall()

                for row in confirmed_orders:
                    order = dict(row)
                    print(f"[Fila] Imprimindo pedido confirmado #{order['id']}...")
                    print_ticket(order)
                    cursor.execute("UPDATE orders SET printed = 1 WHERE id = ?", (order["id"],))
                    conn.commit()
        except Exception as err:
            print(f"Erro ao verificar pedidos para impressão: {err}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        time.sleep(3)


if __name__ == "__main__":
    check_and_print_orders()