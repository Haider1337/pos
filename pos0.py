import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import sqlite3
import os
from PIL import Image, ImageDraw
from barcode import Code128
from barcode.writer import ImageWriter
import csv
from datetime import datetime, timedelta
import logging
import sys
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import seaborn as sns
import numpy as np

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler('pos.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Ensure directories
for dir in ["receipts", "barcodes", "exports"]:
    os.makedirs(dir, exist_ok=True)

# Database Functions
def setup_database():
    with sqlite3.connect("shopify_pos.db") as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS products (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        price REAL,
                        stock INTEGER,
                        category TEXT,
                        barcode TEXT UNIQUE
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS sales (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product_id INTEGER,
                        quantity INTEGER,
                        total REAL,
                        discount REAL DEFAULT 0,
                        date TEXT,
                        staff_id INTEGER,
                        payment_method TEXT,
                        customer_id INTEGER,
                        FOREIGN KEY(product_id) REFERENCES products(id),
                        FOREIGN KEY(staff_id) REFERENCES staff(id),
                        FOREIGN KEY(customer_id) REFERENCES customers(id)
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS customers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        email TEXT UNIQUE,
                        points INTEGER DEFAULT 0,
                        age INTEGER
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS staff (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        pin TEXT UNIQUE,
                        role TEXT
                    )''')
        conn.commit()
    logger.info("Database setup completed successfully.")

def add_product(name, price, stock, category):
    barcode = f"{name}_{price}_{datetime.now().strftime('%H%M%S')}"
    try:
        with sqlite3.connect("shopify_pos.db") as conn:
            c = conn.cursor()
            logger.info(f"Attempting to insert product: name={name}, price={price}, stock={stock}, category={category}, barcode={barcode}")
            c.execute("INSERT INTO products (name, price, stock, category, barcode) VALUES (?, ?, ?, ?, ?)",
                      (name, price, stock, category, barcode))
            conn.commit()
            logger.info(f"Successfully inserted product: {name} with barcode {barcode} into database.")
        barcode_path = generate_barcode(barcode)
        logger.info(f"Barcode generated for {name} at {barcode_path}.")
        return barcode
    except sqlite3.IntegrityError as e:
        logger.error(f"Database integrity error: {e} - Likely duplicate barcode {barcode}")
        raise ValueError(f"Duplicate barcode {barcode}. Try again.")
    except Exception as e:
        logger.error(f"Failed to add product {name}: {e}\n{traceback.format_exc()}")
        raise

def adjust_stock(product_id, new_stock):
    try:
        with sqlite3.connect("shopify_pos.db") as conn:
            c = conn.cursor()
            logger.info(f"Adjusting stock for product ID {product_id} to new value: {new_stock}")
            c.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))
            conn.commit()
            logger.info(f"Stock successfully set to {new_stock} for product ID {product_id}")
    except Exception as e:
        logger.error(f"Failed to adjust stock for product ID {product_id} to {new_stock}: {e}\n{traceback.format_exc()}")
        raise

def delete_product(product_id):
    try:
        with sqlite3.connect("shopify_pos.db") as conn:
            c = conn.cursor()
            logger.info(f"Attempting to delete product with ID {product_id}")
            c.execute("DELETE FROM sales WHERE product_id = ?", (product_id,))
            logger.info(f"Deleted associated sales for product ID {product_id}")
            c.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
            logger.info(f"Product ID {product_id} successfully deleted from database")
    except Exception as e:
        logger.error(f"Failed to delete product ID {product_id}: {e}\n{traceback.format_exc()}")
        raise

def get_inventory(search_term=""):
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if search_term:
            logger.info(f"Searching inventory with term: '{search_term}'")
            c.execute("SELECT * FROM products WHERE name LIKE ? OR id LIKE ? OR barcode LIKE ?", 
                      (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"))
        else:
            logger.info("Retrieving full inventory list")
            c.execute("SELECT * FROM products")
        products = c.fetchall()
    logger.info(f"Retrieved {len(products)} products from inventory with search term '{search_term}'")
    return products

def record_sale(cart_items, staff_id, payment_method, discount=0, customer_id=None):
    with sqlite3.connect("shopify_pos.db") as conn:
        c = conn.cursor()
        conn.execute("BEGIN TRANSACTION")
        try:
            date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            total_sale = 0
            logger.info(f"Recording sale: Staff ID {staff_id}, Payment Method {payment_method}, Discount {discount}, Customer ID {customer_id}, Date {date}")
            for product_id, quantity, price in cart_items:
                c.execute("SELECT stock, name FROM products WHERE id = ?", (product_id,))
                result = c.fetchone()
                stock, name = result["stock"], result["name"]
                logger.info(f"Checking stock for product ID {product_id} ({name}): required {quantity}, available {stock}")
                if stock < quantity:
                    conn.rollback()
                    logger.error(f"Not enough stock for product ID {product_id} ({name}): required {quantity}, available {stock}")
                    return None, None
                total = price * quantity
                total_sale += total
                c.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
                logger.info(f"Updated stock for product ID {product_id} ({name}): reduced by {quantity}, new stock {stock - quantity}")
            total_sale -= discount
            for product_id, quantity, _ in cart_items:
                c.execute("INSERT INTO sales (product_id, quantity, total, discount, date, staff_id, payment_method, customer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (product_id, quantity, total_sale / len(cart_items), discount / len(cart_items), date, staff_id, payment_method, customer_id))
            conn.commit()
            logger.info(f"Sale successfully recorded: Total {total_sale}, Date {date}, Staff ID {staff_id}, Customer ID {customer_id}, Items {len(cart_items)}")
            return total_sale, date
        except Exception as e:
            conn.rollback()
            logger.error(f"Sale recording failed: {e}\n{traceback.format_exc()}")
            return None, None

def add_customer(name, email, points=0, age=None):
    try:
        with sqlite3.connect("shopify_pos.db") as conn:
            c = conn.cursor()
            logger.info(f"Attempting to add customer: name={name}, email={email}, points={points}, age={age}")
            c.execute("INSERT INTO customers (name, email, points, age) VALUES (?, ?, ?, ?)", (name, email, points, age))
            conn.commit()
            customer_id = c.lastrowid
            logger.info(f"Customer {name} successfully added with ID {customer_id}")
            return customer_id
    except sqlite3.IntegrityError:
        with sqlite3.connect("shopify_pos.db") as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM customers WHERE email = ?", (email,))
            customer_id = c.fetchone()[0]
            logger.info(f"Customer {name} already exists with email {email}, ID {customer_id}")
            return customer_id
    except Exception as e:
        logger.error(f"Failed to add customer {name}: {e}\n{traceback.format_exc()}")
        raise

def get_customers():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Retrieving list of customers")
        c.execute("SELECT id, name FROM customers ORDER BY name")
        customers = c.fetchall()
    logger.info(f"Retrieved {len(customers)} customers from database")
    return customers

def get_sales_summary(period="all"):
    with sqlite3.connect("shopify_pos.db") as conn:
        c = conn.cursor()
        if period == "today":
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Fetching sales summary for today since {start_date}")
            c.execute("SELECT SUM(total), SUM(quantity) FROM sales WHERE date >= ?", (start_date,))
        elif period == "week":
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Fetching sales summary for week since {start_date}")
            c.execute("SELECT SUM(total), SUM(quantity) FROM sales WHERE date >= ?", (start_date,))
        elif period == "month":
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Fetching sales summary for month since {start_date}")
            c.execute("SELECT SUM(total), SUM(quantity) FROM sales WHERE date >= ?", (start_date,))
        else:
            logger.info("Fetching all-time sales summary")
            c.execute("SELECT SUM(total), SUM(quantity) FROM sales")
        result = c.fetchone()
        total, items = result[0] or 0, result[1] or 0
    logger.info(f"Sales summary for {period}: Total {total} DZD, Items sold {items}")
    return total, items

def get_avg_sale_value(period="all"):
    with sqlite3.connect("shopify_pos.db") as conn:
        c = conn.cursor()
        if period == "today":
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Calculating average sale value for today since {start_date}")
            c.execute("SELECT AVG(total) FROM sales WHERE date >= ?", (start_date,))
        elif period == "week":
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Calculating average sale value for week since {start_date}")
            c.execute("SELECT AVG(total) FROM sales WHERE date >= ?", (start_date,))
        elif period == "month":
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Calculating average sale value for month since {start_date}")
            c.execute("SELECT AVG(total) FROM sales WHERE date >= ?", (start_date,))
        else:
            logger.info("Calculating all-time average sale value")
            c.execute("SELECT AVG(total) FROM sales")
        result = c.fetchone()
        avg = result[0] or 0
    logger.info(f"Average sale value for {period}: {avg:.2f} DZD")
    return avg

def get_sales_trend(days=7):
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        logger.info(f"Fetching sales trend for last {days} days since {start_date}")
        c.execute("SELECT date(date) as sale_date, SUM(total) as daily_total FROM sales WHERE date >= ? GROUP BY date(date) ORDER BY sale_date", (start_date,))
        trend = c.fetchall()
        logger.info(f"Retrieved sales trend: {len(trend)} days with data - {[(t['sale_date'], t['daily_total']) for t in trend]}")
    return trend

def get_top_products(limit=5):
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info(f"Fetching top {limit} products by quantity sold")
        c.execute("""
            SELECT p.name, SUM(s.quantity) as total_sold, SUM(s.total) as total_revenue 
            FROM sales s 
            JOIN products p ON s.product_id = p.id 
            GROUP BY p.id, p.name 
            ORDER BY total_sold DESC 
            LIMIT ?
        """, (limit,))
        products = c.fetchall()
        logger.info(f"Retrieved {len(products)} top products - {[(p['name'], p['total_sold'], p['total_revenue']) for p in products]}")
    return products

def get_category_sales():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching sales by category")
        c.execute("""
            SELECT p.category, SUM(s.total) as total_sales 
            FROM sales s 
            JOIN products p ON s.product_id = p.id 
            GROUP BY p.category 
            ORDER BY total_sales DESC
        """)
        categories = c.fetchall()
    logger.info(f"Retrieved sales for {len(categories)} categories")
    return categories

def get_staff_performance():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching staff performance data")
        c.execute("""
            SELECT st.name, SUM(s.total) as total_sales, SUM(s.quantity) as items_sold 
            FROM sales s 
            JOIN staff st ON s.staff_id = st.id 
            GROUP BY st.id, st.name 
            ORDER BY total_sales DESC
        """)
        staff = c.fetchall()
    logger.info(f"Retrieved performance for {len(staff)} staff members")
    return staff

def get_top_customer():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching top customer by total spent")
        c.execute("""
            SELECT c.name, SUM(s.total) as total_spent 
            FROM sales s 
            JOIN customers c ON s.customer_id = c.id 
            GROUP BY c.id, c.name 
            ORDER BY total_spent DESC 
            LIMIT 1
        """)
        customer = c.fetchone()
    logger.info(f"Top customer: {customer['name'] if customer else 'None'} with total spent {customer['total_spent'] if customer else 0:.2f} DZD")
    return customer

def get_low_stock():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching low stock items (stock < 5)")
        c.execute("SELECT name, stock FROM products WHERE stock < 5")
        low_stock = c.fetchall()
    logger.info(f"Found {len(low_stock)} items with low stock")
    return low_stock

def get_sales_history(search_term=""):
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if search_term:
            logger.info(f"Fetching sales history with search term: '{search_term}'")
            c.execute("SELECT * FROM sales WHERE product_id LIKE ? OR date LIKE ?", (f"%{search_term}%", f"%{search_term}%"))
        else:
            logger.info("Fetching full sales history")
            c.execute("SELECT * FROM sales")
        sales = c.fetchall()
    logger.info(f"Retrieved {len(sales)} sales records")
    return sales

def get_customer_history(customer_id):
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info(f"Fetching purchase history for customer ID {customer_id}")
        c.execute("SELECT * FROM sales WHERE customer_id = ?", (customer_id,))
        history = c.fetchall()
    logger.info(f"Retrieved {len(history)} sales for customer ID {customer_id}")
    return history

def get_sales_by_season():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching sales by season")
        c.execute("""
            SELECT 
                CASE 
                    WHEN strftime('%m', date) IN ('03', '04', '05') THEN 'Spring'
                    WHEN strftime('%m', date) IN ('06', '07', '08') THEN 'Summer'
                    WHEN strftime('%m', date) IN ('09', '10', '11') THEN 'Fall'
                    ELSE 'Winter'
                END as season,
                SUM(total) as total_sales
            FROM sales
            GROUP BY season
            ORDER BY total_sales DESC
        """)
        seasons = c.fetchall()
        logger.info(f"Retrieved sales for {len(seasons)} seasons - {[(s['season'], s['total_sales']) for s in seasons]}")
    return seasons

def get_sales_by_month():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching sales by month")
        c.execute("""
            SELECT strftime('%m', date) as month, SUM(total) as total_sales
            FROM sales
            GROUP BY month
            ORDER BY month
        """)
        months = c.fetchall()
    logger.info(f"Retrieved sales for {len(months)} months")
    return months

def get_sales_by_age_group():
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info("Fetching sales by age group")
        c.execute("""
            SELECT 
                CASE 
                    WHEN c.age BETWEEN 0 AND 18 THEN '0-18'
                    WHEN c.age BETWEEN 19 AND 30 THEN '19-30'
                    WHEN c.age BETWEEN 31 AND 45 THEN '31-45'
                    WHEN c.age BETWEEN 46 AND 60 THEN '46-60'
                    ELSE '61+'
                END as age_group,
                SUM(s.total) as total_sales
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            WHERE c.age IS NOT NULL
            GROUP BY age_group
            ORDER BY total_sales DESC
        """)
        age_groups = c.fetchall()
    logger.info(f"Retrieved sales for {len(age_groups)} age groups")
    return age_groups

def add_staff(name, pin, role="staff"):
    try:
        with sqlite3.connect("shopify_pos.db") as conn:
            c = conn.cursor()
            logger.info(f"Adding staff: name={name}, pin={pin}, role={role}")
            c.execute("INSERT INTO staff (name, pin, role) VALUES (?, ?, ?)", (name, pin, role))
            conn.commit()
            logger.info(f"Staff {name} added successfully with PIN {pin}")
    except Exception as e:
        logger.error(f"Failed to add staff {name}: {e}\n{traceback.format_exc()}")
        raise

def verify_staff_pin(pin):
    with sqlite3.connect("shopify_pos.db") as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        logger.info(f"Verifying staff PIN: {pin}")
        c.execute("SELECT id, name, role FROM staff WHERE pin = ?", (pin,))
        staff = c.fetchone()
    if staff:
        logger.info(f"Staff verified: {staff['name']} with ID {staff['id']}")
    else:
        logger.warning(f"Staff PIN verification failed for PIN {pin}")
    return staff

def generate_barcode(barcode_value):
    try:
        barcode_path = os.path.join("barcodes", barcode_value)
        logger.info(f"Generating barcode for value: {barcode_value}")
        barcode = Code128(barcode_value, writer=ImageWriter())
        barcode.save(barcode_path, options={"write_text": False})
        full_path = f"{barcode_path}.png"
        if os.path.exists(full_path):
            logger.info(f"Barcode successfully generated at {full_path}")
            return full_path
        raise FileNotFoundError(f"Barcode file {full_path} not created")
    except Exception as e:
        logger.error(f"Barcode generation failed for {barcode_value}: {e}\n{traceback.format_exc()}")
        raise

def print_receipt(receipt_text, filename):
    try:
        logger.info(f"Saving receipt to {filename}")
        with open(filename, "w") as f:
            f.write(receipt_text)
        logger.info(f"Receipt successfully saved to {filename} (printing not implemented)")
        return True
    except Exception as e:
        logger.error(f"Failed to save receipt to {filename}: {e}\n{traceback.format_exc()}")
        return False

def export_to_csv(data, filename, headers):
    try:
        logger.info(f"Exporting data to CSV: {filename} with headers {headers}")
        with open(f"exports/{filename}", "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in data:
                writer.writerow([row[h] for h in headers])
        logger.info(f"Data successfully exported to exports/{filename}")
        return f"exports/{filename}"
    except Exception as e:
        logger.error(f"Failed to export data to {filename}: {e}\n{traceback.format_exc()}")
        raise

# Main Application
# ... (Keep all the imports, logging setup, directory creation, and database functions as they are in your current pos.py)

# Main Application
# ... (Keep all the imports, logging setup, directory creation, and database functions as they are in your current pos.py)

# Main Application
class ShopifyPOS:
    def __init__(self, root):
        self.root = root
        self.root.title("Shopify POS")
        self.root.geometry("1200x800")
        ctk.set_appearance_mode("light")
        self.root.configure(fg_color="#F5F5F5")
        self.cart = []
        self.current_staff = None
        self.font = ("Arial", 12)
        self.header_font = ("Arial", 16, "bold")
        sns.set_style("whitegrid")
        plt.rcParams['font.family'] = 'Arial'
        plt.rcParams['axes.facecolor'] = '#F5F5F5'
        plt.rcParams['figure.facecolor'] = '#F5F5F5'
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.show_login()

    def on_closing(self):
        for after_id in self.root.tk.call('after', 'info'):
            self.root.after_cancel(after_id)
        for attr in ['canvas_trend', 'canvas_products']:
            if hasattr(self, attr) and getattr(self, attr) is not None:
                getattr(self, attr).get_tk_widget().destroy()
                setattr(self, attr, None)
        for attr in ['fig_trend', 'fig_products']:
            if hasattr(self, attr) and getattr(self, attr) is not None:
                plt.close(getattr(self, attr))
                setattr(self, attr, None)
        self.root.destroy()

    def show_login(self):
        self.clear_frame()
        self.login_frame = ctk.CTkFrame(self.root, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        self.login_frame.pack(pady=50, padx=50, expand=True)
        ctk.CTkLabel(self.login_frame, text="Staff Login", font=self.header_font, text_color="#333333").pack(pady=20)
        self.pin_entry = ctk.CTkEntry(self.login_frame, placeholder_text="Enter PIN", show="*", font=self.font, border_color="#E0E0E0")
        self.pin_entry.pack(pady=10)
        ctk.CTkButton(self.login_frame, text="Login", command=self.verify_login, fg_color="#007BFF", hover_color="#0056b3", font=self.font).pack(pady=10)

    def verify_login(self):
        pin = self.pin_entry.get()
        staff = verify_staff_pin(pin)
        if staff:
            self.current_staff = {"id": staff["id"], "name": staff["name"], "role": staff["role"]}
            messagebox.showinfo("Login Success", f"Welcome, {self.current_staff['name']}!")
            self.show_home()
        else:
            messagebox.showwarning("Login Failed", "Invalid PIN.")

    def clear_frame(self):
        for widget in self.root.winfo_children():
            widget.destroy()
        for attr in ['fig_trend', 'ax_trend', 'canvas_trend', 'fig_products', 'ax_products', 'canvas_products']:
            if hasattr(self, attr):
                setattr(self, attr, None)

    def show_home(self):
        self.clear_frame()
        self.home_frame = ctk.CTkFrame(self.root, fg_color="#F5F5F5")
        self.home_frame.pack(fill="both", expand=True)
        header = ctk.CTkLabel(self.home_frame, text="Shopify POS", font=self.header_font, text_color="#333333")
        header.pack(pady=20)
        grid_frame = ctk.CTkFrame(self.home_frame, fg_color="#F5F5F5")
        grid_frame.pack(pady=20)
        tiles = [
            ("New Sale", self.show_sales),
            ("Products", self.show_inventory),
            ("Customers", self.show_customers),
            ("Dashboard", self.show_dashboard),
            ("Settings", self.show_settings)
        ]
        for i, (text, cmd) in enumerate(tiles):
            btn = ctk.CTkButton(grid_frame, text=text, command=cmd, width=150, height=100, corner_radius=10, fg_color="#007BFF", hover_color="#0056b3", font=self.font, text_color="#FFFFFF")
            btn.grid(row=i // 3, column=i % 3, padx=15, pady=15)

    def show_dashboard(self):
        self.clear_frame()
        self.dashboard_frame = ctk.CTkFrame(self.root, fg_color="#F5F5F5")
        self.dashboard_frame.pack(fill="both", expand=True, padx=20, pady=20)
        logger.info("Building Reorganized Modern Dashboard")

        # Grid Configuration
        self.dashboard_frame.grid_rowconfigure(0, weight=0)  # Header
        self.dashboard_frame.grid_rowconfigure(1, weight=0)  # Filter
        self.dashboard_frame.grid_rowconfigure(2, weight=1)  # Middle Row
        self.dashboard_frame.grid_rowconfigure(3, weight=1)  # Bottom Row
        self.dashboard_frame.grid_rowconfigure(4, weight=0)  # Navigation
        self.dashboard_frame.grid_columnconfigure(0, weight=1)
        self.dashboard_frame.grid_columnconfigure(1, weight=1)

        # Header
        ctk.CTkLabel(self.dashboard_frame, text="Dashboard", font=self.header_font, text_color="#333333").grid(row=0, column=0, columnspan=2, pady=10)
        logger.info("Header added")

        # Filter Frame
        self.filter_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        self.filter_frame.grid(row=1, column=0, columnspan=2, pady=10, sticky="ew")
        ctk.CTkLabel(self.filter_frame, text="Filter Period:", font=self.font, text_color="#333333").pack(side="left", padx=5)
        self.period_var = tk.StringVar(value="all")
        period_options = ctk.CTkOptionMenu(self.filter_frame, values=["Today", "Week", "Month", "All Time"], variable=self.period_var, command=self.refresh_dashboard, fg_color="#007BFF", font=self.font)
        period_options.pack(side="left", padx=5)
        ctk.CTkLabel(self.filter_frame, text="Search Product:", font=self.font, text_color="#333333").pack(side="left", padx=5)
        self.search_entry = ctk.CTkEntry(self.filter_frame, placeholder_text="Enter product name", font=self.font, border_color="#E0E0E0")
        self.search_entry.pack(side="left", padx=5, fill="x", expand=True)
        ctk.CTkButton(self.filter_frame, text="Search", command=self.refresh_dashboard, fg_color="#007BFF", font=self.font).pack(side="left", padx=5)
        logger.info("Filter frame added")

        # Middle Row: Sales Overview and Revenue Trend
        middle_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="#F5F5F5")
        middle_frame.grid(row=2, column=0, columnspan=2, pady=10, sticky="nsew")
        middle_frame.grid_columnconfigure(0, weight=1)
        middle_frame.grid_columnconfigure(1, weight=1)
        middle_frame.grid_rowconfigure(0, weight=1)

        # Sales Overview (Left)
        self.sales_frame = ctk.CTkFrame(middle_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0", width=500)
        self.sales_frame.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        ctk.CTkLabel(self.sales_frame, text="Sales Overview", font=("Arial", 14, "bold"), text_color="#333333").pack(pady=10)
        self.sales_grid = ctk.CTkFrame(self.sales_frame, fg_color="#FFFFFF")
        self.sales_grid.pack(fill="x", padx=15, pady=10)
        logger.info("Sales Overview frame added")

        # Revenue Trend (Right)
        self.trend_frame = ctk.CTkFrame(middle_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0", width=500)
        self.trend_frame.grid(row=0, column=1, padx=15, pady=15, sticky="nsew")
        logger.info("Revenue Trend frame added")

        # Bottom Row: Top Products/Winner and Modern Bar Chart
        bottom_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="#F5F5F5")
        bottom_frame.grid(row=3, column=0, columnspan=2, pady=10, sticky="nsew")
        bottom_frame.grid_columnconfigure(0, weight=1)
        bottom_frame.grid_columnconfigure(1, weight=1)
        bottom_frame.grid_rowconfigure(0, weight=1)

        # Left: Top Products and Winner
        self.left_bottom_frame = ctk.CTkFrame(bottom_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0", width=500)
        self.left_bottom_frame.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        self.left_bottom_frame.grid_rowconfigure(0, weight=1)
        self.left_bottom_frame.grid_rowconfigure(1, weight=0)
        self.left_bottom_frame.grid_columnconfigure(0, weight=1)

        products_card = ctk.CTkFrame(self.left_bottom_frame, fg_color="#FFFFFF")
        products_card.grid(row=0, column=0, pady=10, sticky="nsew")
        ctk.CTkLabel(products_card, text="Top Products", font=("Arial", 14, "bold"), text_color="#333333").pack(pady=10, anchor="w", padx=15)
        self.top_products_label = ctk.CTkLabel(products_card, text="", font=("Arial", 12), text_color="#333333", wraplength=450, justify="left")
        self.top_products_label.pack(anchor="w", padx=15, pady=5)
        ctk.CTkButton(products_card, text="Export", command=lambda: self.export_section(get_top_products(), "top_products.csv", ["name", "total_sold", "total_revenue"]), fg_color="#007BFF", font=self.font).pack(anchor="e", padx=15, pady=5)

        winner_card = ctk.CTkFrame(self.left_bottom_frame, fg_color="#FFFFFF")
        winner_card.grid(row=1, column=0, pady=10, sticky="ew")
        ctk.CTkLabel(winner_card, text="Winner Product", font=("Arial", 14, "bold"), text_color="#333333").pack(pady=10, anchor="w", padx=15)
        self.winner_label = ctk.CTkLabel(winner_card, text="Loading...", font=("Arial", 12), text_color="#333333", justify="left")
        self.winner_label.pack(anchor="w", padx=15)
        logger.info("Top Products and Winner frames added")

        # Right: Modern Top Products by Revenue Horizontal Bar Chart
        self.right_bottom_frame = ctk.CTkFrame(bottom_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0", width=500)
        self.right_bottom_frame.grid(row=0, column=1, padx=15, pady=15, sticky="nsew")
        self.right_bottom_frame.grid_columnconfigure(0, weight=1)
        self.right_bottom_frame.grid_rowconfigure(0, weight=1)
        logger.info("Bottom right frame for Modern Bar chart added")

        # Navigation Frame
        self.nav_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="#F5F5F5")
        self.nav_frame.grid(row=4, column=0, columnspan=2, pady=10, sticky="ew")
        ctk.CTkButton(self.nav_frame, text="Back", command=self.show_home, fg_color="#007BFF", font=self.font).pack(side="left", padx=10)
        ctk.CTkButton(self.nav_frame, text="Refresh", command=self.refresh_dashboard, fg_color="#007BFF", font=self.font).pack(side="left", padx=10)
        logger.info("Navigation frame added")

        self.refresh_dashboard()

    def refresh_dashboard(self, event=None):
        period = self.period_var.get().lower()
        search_term = self.search_entry.get().strip()

        # Sales Overview
        for widget in self.sales_grid.winfo_children():
            widget.destroy()
        periods = [("Today", "today"), ("Week", "week"), ("Month", "month"), ("All Time", "all")]
        for i, (label, p) in enumerate(periods):
            t, q = get_sales_summary(p)
            a = get_avg_sale_value(p)
            ctk.CTkLabel(self.sales_grid, text=f"{label}: {t:.2f} DZD\n{q} items\nAvg: {a:.2f} DZD", font=self.font, text_color="#333333").grid(row=0, column=i, padx=5, pady=5)
        logger.info("Sales Overview refreshed")

        # Top Products
        top_products = get_top_products(limit=5)
        if top_products:
            self.top_products_label.configure(text="\n".join([f"{p['name']}: {p['total_sold']} sold, {p['total_revenue']:.2f} DZD" for p in top_products]))
        else:
            self.top_products_label.configure(text="No sales data available.")
        logger.info("Top Products refreshed")

        # Winner Product
        if top_products:
            winner = top_products[0]
            self.winner_label.configure(text=f"Top Product: {winner['name']}\nRevenue: {winner['total_revenue']:.2f} DZD\nUnits: {winner['total_sold']}")
        else:
            self.winner_label.configure(text="No sales data available.")
        logger.info("Winner Product refreshed")

        # Clear previous charts
        for widget in self.trend_frame.winfo_children():
            widget.destroy()
        for widget in self.right_bottom_frame.winfo_children():
            widget.destroy()

        # Revenue Trend Chart
        self.fig_trend, self.ax_trend = plt.subplots(figsize=(6, 4))
        trend_days = 30 if period == "month" else 7 if period == "week" else 1 if period == "today" else 365
        trend_data = get_sales_trend(days=trend_days)
        if trend_data:
            self.ax_trend.plot([d["sale_date"] for d in trend_data], [d["daily_total"] for d in trend_data], color="#007BFF", marker='o', linewidth=2)
            self.ax_trend.set_title("Revenue Trend", fontsize=12, color="#333333")
            self.ax_trend.set_xlabel("Date", fontsize=10, color="#333333")
            self.ax_trend.set_ylabel("Revenue (DZD)", fontsize=10, color="#333333")
            self.ax_trend.tick_params(axis='x', rotation=45, labelsize=8, colors="#333333")
            self.ax_trend.tick_params(axis='y', labelsize=8, colors="#333333")
        else:
            self.ax_trend.text(0.5, 0.5, "No Sales Data", ha='center', va='center', fontsize=10, color="#333333")
            self.ax_trend.set_title("Revenue Trend", fontsize=12, color="#333333")
        self.fig_trend.tight_layout()
        self.canvas_trend = FigureCanvasTkAgg(self.fig_trend, master=self.trend_frame)
        self.canvas_trend.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
        self.canvas_trend.draw()
        self.canvas_trend.get_tk_widget().update()
        logger.info("Revenue Trend chart refreshed")

        # Modern Top Products by Revenue Horizontal Bar Chart
        self.fig_products, self.ax_products = plt.subplots(figsize=(6, 4))
        if top_products:
            products = [p["name"] for p in top_products]
            revenues = [p["total_revenue"] for p in top_products]
            
            # Create horizontal bars with a modern gradient
            bars = self.ax_products.barh(products, revenues, color=plt.cm.Blues(np.linspace(0.2, 0.8, len(products))))
            
            # Add value labels on the bars
            for bar in bars:
                width = bar.get_width()
                self.ax_products.text(width, bar.get_y() + bar.get_height()/2, f'{width:.2f} DZD', 
                                   ha='left', va='center', fontweight='bold', fontsize=8, color='white')
            
            # Customize the chart for a modern look
            self.ax_products.set_title("Top Products by Revenue", fontsize=12, color="#333333", pad=15)
            self.ax_products.set_xlabel("Revenue (DZD)", fontsize=10, color="#333333")
            self.ax_products.set_ylabel("Product", fontsize=10, color="#333333")
            self.ax_products.tick_params(axis='x', labelsize=8, colors="#333333")
            self.ax_products.tick_params(axis='y', labelsize=8, colors="#333333")
            self.ax_products.spines['top'].set_visible(False)
            self.ax_products.spines['right'].set_visible(False)
            self.ax_products.spines['left'].set_color('#333333')
            self.ax_products.spines['bottom'].set_color('#333333')
            self.ax_products.set_facecolor('#F5F5F5')
            self.ax_products.grid(axis='x', linestyle='--', alpha=0.7, color='#999999')
        else:
            self.ax_products.text(0.5, 0.5, "No Sales Data", ha='center', va='center', fontsize=10, color="#333333")
            self.ax_products.set_title("Top Products by Revenue", fontsize=12, color="#333333")
        self.fig_products.tight_layout()
        self.canvas_products = FigureCanvasTkAgg(self.fig_products, master=self.right_bottom_frame)
        self.canvas_products.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
        self.canvas_products.draw()
        self.canvas_products.get_tk_widget().update()
        logger.info("Modern Top Products Bar chart refreshed")

        logger.info("Dashboard refreshed with period: %s, search: %s", period, search_term)

    def show_inventory(self):
        self.clear_frame()
        self.inventory_frame = ctk.CTkFrame(self.root, fg_color="#F5F5F5")
        self.inventory_frame.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(self.inventory_frame, text="Products", font=self.header_font, text_color="#333333").pack(pady=10)
        form_frame = ctk.CTkFrame(self.inventory_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        form_frame.pack(fill="x", pady=10)
        self.inv_name = ctk.CTkEntry(form_frame, placeholder_text="Name", font=self.font, border_color="#E0E0E0")
        self.inv_price = ctk.CTkEntry(form_frame, placeholder_text="Price (DZD)", font=self.font, border_color="#E0E0E0")
        self.inv_stock = ctk.CTkEntry(form_frame, placeholder_text="Stock", font=self.font, border_color="#E0E0E0")
        self.inv_category = ctk.CTkEntry(form_frame, placeholder_text="Category", font=self.font, border_color="#E0E0E0")
        for i, entry in enumerate([self.inv_name, self.inv_price, self.inv_stock, self.inv_category]):
            entry.grid(row=0, column=i, padx=5, pady=5)
        ctk.CTkButton(form_frame, text="Add Product", command=self.add_product, fg_color="#007BFF", font=self.font).grid(row=1, column=0, columnspan=2, pady=5, padx=5)
        ctk.CTkButton(form_frame, text="Remove Item", command=self.remove_inventory_item, fg_color="#007BFF", font=self.font).grid(row=1, column=2, pady=5, padx=5)
        ctk.CTkButton(form_frame, text="Adjust Stock", command=self.adjust_stock_dialog, fg_color="#007BFF", font=self.font).grid(row=1, column=3, pady=5, padx=5)
        search_frame = ctk.CTkFrame(self.inventory_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        search_frame.pack(fill="x", pady=10)
        self.inv_search = ctk.CTkEntry(search_frame, placeholder_text="Search by Name/ID/Barcode", font=self.font, border_color="#E0E0E0")
        self.inv_search.pack(side="left", padx=10, pady=5, fill="x", expand=True)
        ctk.CTkButton(search_frame, text="Search", command=self.refresh_inventory, fg_color="#007BFF", font=self.font).pack(side="right", padx=10, pady=5)
        self.inv_table = ttk.Treeview(self.inventory_frame, columns=("ID", "Name", "Price", "Stock", "Category", "Barcode"), show="headings")
        for col in ("ID", "Name", "Price", "Stock", "Category", "Barcode"):
            self.inv_table.heading(col, text=col)
            self.inv_table.column(col, width=100)
        self.inv_table.pack(fill="both", expand=True, pady=10)
        self.inv_low_stock = ctk.CTkLabel(self.inventory_frame, text="Low Stock Alerts: None", font=self.font, text_color="#333333")
        self.inv_low_stock.pack(pady=5)
        ctk.CTkButton(self.inventory_frame, text="Back", command=self.show_home, fg_color="#007BFF", font=self.font).pack(pady=10)
        self.refresh_inventory()

    def show_sales(self):
        self.clear_frame()
        self.sales_frame = ctk.CTkFrame(self.root, fg_color="#F5F5F5")
        self.sales_frame.pack(fill="both", expand=True, padx=20, pady=20)
        self.sales_frame.grid_rowconfigure(0, weight=0)
        self.sales_frame.grid_rowconfigure(1, weight=0)
        self.sales_frame.grid_rowconfigure(2, weight=1)
        self.sales_frame.grid_rowconfigure(3, weight=0)
        self.sales_frame.grid_rowconfigure(4, weight=1)
        self.sales_frame.grid_rowconfigure(5, weight=0)
        self.sales_frame.grid_columnconfigure(0, weight=1)
        header = ctk.CTkLabel(self.sales_frame, text="New Sale", font=self.header_font, text_color="#333333")
        header.grid(row=0, column=0, pady=10, sticky="n")
        sale_frame = ctk.CTkFrame(self.sales_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        sale_frame.grid(row=1, column=0, pady=10, sticky="ew")
        self.sale_product_id = ctk.CTkEntry(sale_frame, placeholder_text="Product ID", font=self.font, border_color="#E0E0E0")
        self.sale_quantity = ctk.CTkEntry(sale_frame, placeholder_text="Quantity", font=self.font, border_color="#E0E0E0")
        self.sale_customer = ctk.CTkComboBox(sale_frame, values=["None"] + [c["name"] for c in get_customers()], font=self.font)
        self.sale_payment = ctk.CTkComboBox(sale_frame, values=["Cash", "Card"], font=self.font)
        self.sale_discount = ctk.CTkEntry(sale_frame, placeholder_text="Discount (DZD)", font=self.font, border_color="#E0E0E0")
        for i, widget in enumerate([self.sale_product_id, self.sale_quantity, self.sale_customer, self.sale_payment, self.sale_discount]):
            widget.grid(row=0, column=i, padx=5, pady=5)
        ctk.CTkButton(sale_frame, text="Add to Cart", command=self.add_to_cart, fg_color="#007BFF", font=self.font).grid(row=1, column=0, pady=5, padx=5)
        ctk.CTkButton(sale_frame, text="Preview Receipt", command=self.preview_receipt, fg_color="#007BFF", font=self.font).grid(row=1, column=1, pady=5, padx=5)
        ctk.CTkButton(sale_frame, text="Finalize Sale", command=self.finalize_sale, fg_color="#007BFF", font=self.font).grid(row=1, column=2, pady=5, padx=5)
        self.cart_table = ttk.Treeview(self.sales_frame, columns=("ID", "Name", "Quantity", "Subtotal"), show="headings")
        for col in ("ID", "Name", "Quantity", "Subtotal"):
            self.cart_table.heading(col, text=col)
            self.cart_table.column(col, width=150)
        self.cart_table.grid(row=2, column=0, pady=10, sticky="nsew")
        history_frame = ctk.CTkFrame(self.sales_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        history_frame.grid(row=3, column=0, pady=10, sticky="ew")
        self.sales_search = ctk.CTkEntry(history_frame, placeholder_text="Search by ID/Date", font=self.font, border_color="#E0E0E0")
        self.sales_search.pack(side="left", padx=10, pady=5, fill="x", expand=True)
        ctk.CTkButton(history_frame, text="Search", command=self.refresh_sales, fg_color="#007BFF", font=self.font).pack(side="right", padx=10, pady=5)
        self.sales_table = ttk.Treeview(self.sales_frame, columns=("ID", "Product ID", "Quantity", "Total", "Discount", "Date", "Staff", "Payment", "Customer"), show="headings")
        for col in ("ID", "Product ID", "Quantity", "Total", "Discount", "Date", "Staff", "Payment", "Customer"):
            self.sales_table.heading(col, text=col)
            self.sales_table.column(col, width=100)
        self.sales_table.grid(row=4, column=0, pady=10, sticky="nsew")
        button_frame = ctk.CTkFrame(self.sales_frame, fg_color="#F5F5F5")
        button_frame.grid(row=5, column=0, pady=10, sticky="s")
        ctk.CTkButton(button_frame, text="Clear Cart", command=self.clear_cart, fg_color="#007BFF", font=self.font).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="Remove Item", command=self.remove_cart_item, fg_color="#007BFF", font=self.font).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="Process Return", command=self.process_return, fg_color="#007BFF", font=self.font).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="Back", command=self.show_home, fg_color="#007BFF", font=self.font).pack(side="left", padx=5)
        self.refresh_sales()

    def show_customers(self):
        self.clear_frame()
        self.customers_frame = ctk.CTkFrame(self.root, fg_color="#F5F5F5")
        self.customers_frame.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(self.customers_frame, text="Customers", font=self.header_font, text_color="#333333").pack(pady=10)
        customer_frame = ctk.CTkFrame(self.customers_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        customer_frame.pack(fill="x", pady=10)
        self.cust_name = ctk.CTkEntry(customer_frame, placeholder_text="Name", font=self.font, border_color="#E0E0E0")
        self.cust_email = ctk.CTkEntry(customer_frame, placeholder_text="Email", font=self.font, border_color="#E0E0E0")
        self.cust_points = ctk.CTkEntry(customer_frame, placeholder_text="Points", font=self.font, border_color="#E0E0E0")
        self.cust_age = ctk.CTkEntry(customer_frame, placeholder_text="Age", font=self.font, border_color="#E0E0E0")
        for i, entry in enumerate([self.cust_name, self.cust_email, self.cust_points, self.cust_age]):
            entry.grid(row=0, column=i, padx=5, pady=5)
        ctk.CTkButton(customer_frame, text="Add Customer", command=self.add_customer, fg_color="#007BFF", font=self.font).grid(row=1, column=0, columnspan=4, pady=5)
        search_frame = ctk.CTkFrame(self.customers_frame, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color="#E0E0E0")
        search_frame.pack(fill="x", pady=10)
        self.cust_search = ctk.CTkEntry(search_frame, placeholder_text="Search by Name/Email", font=self.font, border_color="#E0E0E0")
        self.cust_search.pack(side="left", padx=10, pady=5, fill="x", expand=True)
        ctk.CTkButton(search_frame, text="Search", command=self.refresh_customers, fg_color="#007BFF", font=self.font).pack(side="right", padx=10, pady=5)
        self.cust_table = ttk.Treeview(self.customers_frame, columns=("Name", "Email", "Points"), show="headings")
        for col in ("Name", "Email", "Points"):
            self.cust_table.heading(col, text=col)
            self.cust_table.column(col, width=200)
        self.cust_table.pack(fill="both", expand=True, pady=10)
        self.cust_table.bind("<Double-1>", self.show_customer_history)
        ctk.CTkButton(self.customers_frame, text="Back", command=self.show_home, fg_color="#007BFF", font=self.font).pack(pady=10)
        self.refresh_customers()

    def show_settings(self):
        self.clear_frame()
        self.settings_frame = ctk.CTkFrame(self.root, fg_color="#F5F5F5")
        self.settings_frame.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(self.settings_frame, text="Settings", font=self.header_font, text_color="#333333").pack(pady=10)
        ctk.CTkLabel(self.settings_frame, text="Receipt Branding: Shopify POS", font=self.font, text_color="#333333").pack(pady=5)
        ctk.CTkLabel(self.settings_frame, text="Printer: Default", font=self.font, text_color="#333333").pack(pady=5)
        ctk.CTkButton(self.settings_frame, text="Back", command=self.show_home, fg_color="#007BFF", font=self.font).pack(pady=10)

    def add_product(self):
        try:
            name = self.inv_name.get().strip()
            price = float(self.inv_price.get().strip() or 0)
            stock = int(self.inv_stock.get().strip() or 0)
            category = self.inv_category.get().strip()
            if not name:
                raise ValueError("Product name cannot be empty.")
            if price < 0 or stock < 0:
                raise ValueError("Price and stock must be non-negative.")
            barcode = add_product(name, price, stock, category)
            messagebox.showinfo("Success", f"Added {name} with barcode {barcode}")
            self.inv_name.delete(0, tk.END)
            self.inv_price.delete(0, tk.END)
            self.inv_stock.delete(0, tk.END)
            self.inv_category.delete(0, tk.END)
            self.refresh_inventory()
        except ValueError as e:
            messagebox.showwarning("Error", str(e))
        except Exception as e:
            messagebox.showwarning("Error", f"Failed to add product: {str(e)}. Check pos.log.")

    def remove_inventory_item(self):
        selected = self.inv_table.selection()
        if not selected:
            messagebox.showwarning("Error", "Select an item.")
            return
        product_id = int(self.inv_table.item(selected[0], "values")[0])
        name = self.inv_table.item(selected[0], "values")[1]
        if messagebox.askyesno("Confirm", f"Remove {name} (ID: {product_id})?"):
            delete_product(product_id)
            messagebox.showinfo("Success", f"Removed {name}")
            self.refresh_inventory()

    def adjust_stock_dialog(self):
        selected = self.inv_table.selection()
        if not selected:
            messagebox.showwarning("Error", "Select an item.")
            return
        product_id = int(self.inv_table.item(selected[0], "values")[0])
        name = self.inv_table.item(selected[0], "values")[1]
        current_stock = int(self.inv_table.item(selected[0], "values")[3])
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Set Stock")
        dialog.configure(fg_color="#F5F5F5")
        ctk.CTkLabel(dialog, text=f"Set stock for {name} (Current: {current_stock})", font=self.font, text_color="#333333").pack(pady=5)
        entry = ctk.CTkEntry(dialog, placeholder_text="Enter new stock value", font=self.font, border_color="#E0E0E0")
        entry.pack(pady=5)
        ctk.CTkButton(dialog, text="Apply", command=lambda: self.apply_stock_adjust(product_id, entry.get(), dialog), fg_color="#007BFF", font=self.font).pack(pady=5)

    def apply_stock_adjust(self, product_id, new_stock, dialog):
        try:
            new_stock = int(new_stock)
            if new_stock < 0:
                raise ValueError("Stock cannot be negative.")
            adjust_stock(product_id, new_stock)
            messagebox.showinfo("Success", f"Stock set to {new_stock}.")
            self.refresh_inventory()
            dialog.destroy()
        except ValueError as e:
            messagebox.showwarning("Error", str(e))

    def refresh_inventory(self):
        for item in self.inv_table.get_children():
            self.inv_table.delete(item)
        products = get_inventory(self.inv_search.get())
        for p in products:
            self.inv_table.insert("", "end", values=(p["id"], p["name"], f"{p['price']:.2f}", p["stock"], p["category"], p["barcode"]))
        low_stock = get_low_stock()
        self.inv_low_stock.configure(text=f"Low Stock Alerts: {', '.join([p['name'] for p in low_stock]) or 'None'}")

    def add_to_cart(self):
        try:
            product_id = int(self.sale_product_id.get())
            qty = int(self.sale_quantity.get())
            if qty <= 0:
                raise ValueError("Quantity must be positive.")
            with sqlite3.connect("shopify_pos.db") as conn:
                c = conn.cursor()
                c.execute("SELECT price, stock, name FROM products WHERE id = ?", (product_id,))
                result = c.fetchone()
                if not result or result[1] < qty:
                    raise ValueError("Not enough stock or invalid ID.")
                price, _, name = result
                self.cart.append((product_id, qty, price, name))
                logger.info(f"Added to cart: Product ID {product_id}, Quantity {qty}, Name {name}, Price {price}")
                self.update_cart_display()
            self.sale_product_id.delete(0, tk.END)
            self.sale_quantity.delete(0, tk.END)
            self.sale_product_id.focus()
        except ValueError as e:
            messagebox.showwarning("Error", str(e))

    def update_cart_display(self):
        for item in self.cart_table.get_children():
            self.cart_table.delete(item)
        for product_id, qty, price, name in self.cart:
            subtotal = price * qty
            self.cart_table.insert("", "end", values=(product_id, name, qty, f"{subtotal:.2f}"))

    def remove_cart_item(self):
        selected = self.cart_table.selection()
        if not selected:
            messagebox.showwarning("Error", "Select an item.")
            return
        index = int(self.cart_table.index(selected[0]))
        name = self.cart_table.item(selected[0], "values")[1]
        if messagebox.askyesno("Confirm", f"Remove {name} from cart?"):
            del self.cart[index]
            logger.info(f"Removed item from cart: {name}")
            self.update_cart_display()

    def preview_receipt(self):
        if not self.cart:
            messagebox.showwarning("Error", "Cart is empty!")
            return
        discount = float(self.sale_discount.get() or 0)
        customer_name = self.sale_customer.get()
        receipt_lines = ["Shopify POS Receipt"]
        total = 0
        for _, qty, price, name in self.cart:
            subtotal = price * qty
            total += subtotal
            receipt_lines.append(f"Item: {name}")
            receipt_lines.append(f"Quantity: {qty}")
            receipt_lines.append(f"Subtotal: {subtotal:.2f} DZD")
        receipt_lines.append(f"Discount: {discount:.2f} DZD")
        receipt_lines.append(f"Total: {total - discount:.2f} DZD")
        receipt_lines.append(f"Payment Method: {self.sale_payment.get()}")
        receipt_lines.append(f"Customer: {customer_name if customer_name != 'None' else 'N/A'}")
        receipt_lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        receipt_lines.append(f"Staff: {self.current_staff['name']}")
        receipt = "\n".join(receipt_lines)
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Receipt Preview")
        dialog.configure(fg_color="#F5F5F5")
        text = ctk.CTkTextbox(dialog, width=300, height=400, font=self.font, fg_color="#FFFFFF", border_color="#E0E0E0", text_color="#333333")
        text.insert("0.0", receipt)
        text.configure(state="disabled")
        text.pack(pady=10)
        ctk.CTkButton(dialog, text="Close", command=dialog.destroy, fg_color="#007BFF", font=self.font).pack(pady=5)

    def finalize_sale(self):
        if not self.cart:
            messagebox.showwarning("Error", "Cart is empty!")
            return
        discount = float(self.sale_discount.get() or 0)
        customer_name = self.sale_customer.get()
        customer_id = None
        if customer_name != "None":
            with sqlite3.connect("shopify_pos.db") as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM customers WHERE name = ?", (customer_name,))
                result = c.fetchone()
                customer_id = result[0] if result else None
                logger.info(f"Selected customer for sale: {customer_name}, ID {customer_id}")
        total, sale_date = record_sale([(pid, qty, price) for pid, qty, price, _ in self.cart], self.current_staff["id"], self.sale_payment.get(), discount, customer_id)
        if total:
            receipt_lines = ["Shopify POS Receipt"]
            for _, qty, price, name in self.cart:
                subtotal = price * qty
                receipt_lines.append(f"Item: {name}")
                receipt_lines.append(f"Quantity: {qty}")
                receipt_lines.append(f"Subtotal: {subtotal:.2f} DZD")
            receipt_lines.append(f"Discount: {discount:.2f} DZD")
            receipt_lines.append(f"Total: {total:.2f} DZD")
            receipt_lines.append(f"Payment Method: {self.sale_payment.get()}")
            receipt_lines.append(f"Customer: {customer_name if customer_name != 'None' else 'N/A'}")
            receipt_lines.append(f"Date: {sale_date}")
            receipt_lines.append(f"Staff: {self.current_staff['name']}")
            receipt = "\n".join(receipt_lines)
            receipt_filename = f"receipts/receipt_{sale_date.replace(':', '-')}.txt"
            print_status = "Printed" if print_receipt(receipt, receipt_filename) else "Saved"
            messagebox.showinfo("Sale Processed", f"{receipt}\n{print_status} to {receipt_filename}")
            self.cart.clear()
            self.update_cart_display()
            self.sale_discount.delete(0, tk.END)
            self.sale_customer.set("None")
            self.refresh_sales()
        else:
            messagebox.showwarning("Error", "Sale failed.")

    def clear_cart(self):
        self.cart.clear()
        self.update_cart_display()
        self.sale_discount.delete(0, tk.END)
        self.sale_customer.set("None")
        logger.info("Cart cleared.")

    def process_return(self):
        selected = self.sales_table.selection()
        if not selected:
            messagebox.showwarning("Error", "Select a sale.")
            return
        sale_id = int(self.sales_table.item(selected[0], "values")[0])
        product_id = int(self.sales_table.item(selected[0], "values")[1])
        qty = int(self.sales_table.item(selected[0], "values")[2])
        total = float(self.sales_table.item(selected[0], "values")[3].replace(" DZD", ""))
        if messagebox.askyesno("Confirm", f"Return {qty} items for {total:.2f} DZD?"):
            with sqlite3.connect("shopify_pos.db") as conn:
                c = conn.cursor()
                logger.info(f"Processing return: Sale ID {sale_id}, Product ID {product_id}, Quantity {qty}, Total {total}")
                c.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
                c.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (qty, product_id))
                conn.commit()
                logger.info(f"Return processed successfully for Sale ID {sale_id}")
            messagebox.showinfo("Success", "Return processed.")
            self.refresh_sales()

    def refresh_sales(self):
        for item in self.sales_table.get_children():
            self.sales_table.delete(item)
        sales = get_sales_history(self.sales_search.get())
        with sqlite3.connect("shopify_pos.db") as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            for sale in sales:
                c.execute("SELECT name FROM staff WHERE id = ?", (sale["staff_id"],))
                staff_result = c.fetchone()
                staff_name = staff_result["name"] if staff_result else "N/A"
                customer_name = "N/A"
                if sale["customer_id"]:
                    c.execute("SELECT name FROM customers WHERE id = ?", (sale["customer_id"],))
                    customer_result = c.fetchone()
                    customer_name = customer_result["name"] if customer_result else "N/A"
                self.sales_table.insert("", "end", values=(sale["id"], sale["product_id"], sale["quantity"], f"{sale['total']:.2f}", 
                                                           f"{sale['discount']:.2f}", sale["date"], staff_name, sale["payment_method"], customer_name))

    def export_sales(self):
        filename = export_sales_to_csv()
        messagebox.showinfo("Success", f"Sales exported to {filename}")

    def export_section(self, data, filename, headers):
        filename = export_to_csv(data, filename, headers)
        messagebox.showinfo("Success", f"Exported to {filename}")

    def add_customer(self):
        try:
            name = self.cust_name.get()
            email = self.cust_email.get()
            points = int(self.cust_points.get() or 0)
            age = int(self.cust_age.get() or 0) if self.cust_age.get() else None
            if not name:
                raise ValueError("Name cannot be empty.")
            add_customer(name, email, points, age)
            messagebox.showinfo("Success", f"Added {name}")
            self.cust_name.delete(0, tk.END)
            self.cust_email.delete(0, tk.END)
            self.cust_points.delete(0, tk.END)
            self.cust_age.delete(0, tk.END)
            self.refresh_customers()
            if hasattr(self, 'sale_customer'):
                self.sale_customer.configure(values=["None"] + [c["name"] for c in get_customers()])
        except ValueError as e:
            messagebox.showwarning("Error", str(e))

    def refresh_customers(self):
        for item in self.cust_table.get_children():
            self.cust_table.delete(item)
        with sqlite3.connect("shopify_pos.db") as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            search = self.cust_search.get()
            if search:
                c.execute("SELECT name, email, points FROM customers WHERE name LIKE ? OR email LIKE ?", (f"%{search}%", f"%{search}%"))
            else:
                c.execute("SELECT name, email, points FROM customers")
            for cust in c.fetchall():
                self.cust_table.insert("", "end", values=(cust["name"], cust["email"], cust["points"]))

    def show_customer_history(self, event):
        selected = self.cust_table.selection()
        if not selected:
            return
        email = self.cust_table.item(selected[0], "values")[1]
        with sqlite3.connect("shopify_pos.db") as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT id FROM customers WHERE email = ?", (email,))
            customer_id = c.fetchone()["id"]
            history = get_customer_history(customer_id)
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(f"History for {self.cust_table.item(selected[0], 'values')[0]}")
        dialog.configure(fg_color="#F5F5F5")
        table = ttk.Treeview(dialog, columns=("ID", "Product ID", "Quantity", "Total", "Discount", "Date"), show="headings")
        for col in ("ID", "Product ID", "Quantity", "Total", "Discount", "Date"):
            table.heading(col, text=col)
            table.column(col, width=100)
        for sale in history:
            table.insert("", "end", values=(sale["id"], sale["product_id"], sale["quantity"], f"{sale['total']:.2f}", f"{sale['discount']:.2f}", sale["date"]))
        table.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkButton(dialog, text="Close", command=dialog.destroy, fg_color="#007BFF", font=self.font).pack(pady=5)

if __name__ == "__main__":
    setup_database()
    with sqlite3.connect("shopify_pos.db") as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO staff (id, name, pin, role) VALUES (1, 'Admin', '1234', 'admin')")
       # c.execute("INSERT OR IGNORE INTO products (id, name, price, stock, category, barcode) VALUES (1, 'Pen', 5.0, 10, 'Stationery', 'Pen_5_Initial')")
       # c.execute("INSERT OR IGNORE INTO sales (product_id, quantity, total, discount, date, staff_id, payment_method) VALUES (1, 2, 10.0, 0, ?, 1, 'Cash')", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()
        logger.info("Test data inserted: Pen, 2 sold, 10 DZD")
    root = ctk.CTk()
    app = ShopifyPOS(root)
    root.mainloop()