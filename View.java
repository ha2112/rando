package model;

import javax.swing.*;
import java.awt.*;
import java.awt.event.*;
import java.util.List;

public class View extends JFrame {
    private JTextField usernameField;
    private JPasswordField passwordField;
    private JButton loginButton;
    private JPanel mainPanel;
    private JPanel productPanel;
    private JPanel cartPanel;
    private ShoppingCart cart;
    private User currentUser;
    private Customer currentCustomer;

    public View() {
        // Initialize shopping cart
        cart = new ShoppingCart();
        
        // Set up main frame
        setTitle("E-Commerce System");
        setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
        setSize(800, 600);
        setLayout(new BorderLayout());

        // Create login panel
        createLoginPanel();
        
        // Create main content panel (initially invisible)
        mainPanel = new JPanel(new BorderLayout());
        mainPanel.setVisible(false);
        
        // Create product display panel
        createProductPanel();
        
        // Create shopping cart panel
        createCartPanel();
        
        mainPanel.add(productPanel, BorderLayout.CENTER);
        mainPanel.add(cartPanel, BorderLayout.EAST);
        
        add(mainPanel);
        
        setLocationRelativeTo(null);
    }

    private void createLoginPanel() {
        JPanel loginPanel = new JPanel(new GridBagLayout());
        GridBagConstraints gbc = new GridBagConstraints();
        
        usernameField = new JTextField(20);
        passwordField = new JPasswordField(20);
        loginButton = new JButton("Login");
        
        gbc.gridx = 0;
        gbc.gridy = 0;
        gbc.insets = new Insets(5,5,5,5);
        loginPanel.add(new JLabel("Username:"), gbc);
        
        gbc.gridx = 1;
        loginPanel.add(usernameField, gbc);
        
        gbc.gridx = 0;
        gbc.gridy = 1;
        loginPanel.add(new JLabel("Password:"), gbc);
        
        gbc.gridx = 1;
        loginPanel.add(passwordField, gbc);
        
        gbc.gridx = 1;
        gbc.gridy = 2;
        loginPanel.add(loginButton, gbc);
        
        loginButton.addActionListener(e -> handleLogin());
        
        add(loginPanel, BorderLayout.CENTER);
    }

    private void createProductPanel() {
        productPanel = new JPanel();
        productPanel.setLayout(new BoxLayout(productPanel, BoxLayout.Y_AXIS));
        productPanel.setBorder(BorderFactory.createTitledBorder("Products"));
        
        // Sample products - in real app would load from database
        addProductToPanel(new Product(1, "Laptop", 999.99, 10));
        addProductToPanel(new Product(2, "Phone", 599.99, 20));
        addProductToPanel(new Product(3, "Tablet", 299.99, 15));
    }

    private void addProductToPanel(Product product) {
        JPanel productItemPanel = new JPanel(new FlowLayout(FlowLayout.LEFT));
        productItemPanel.add(new JLabel(product.getName() + " - $" + product.getPrice()));
        JButton addToCartButton = new JButton("Add to Cart");
        addToCartButton.addActionListener(e -> {
            cart.addProductToCart(product);
            updateCartPanel();
        });
        productItemPanel.add(addToCartButton);
        productPanel.add(productItemPanel);
    }

    private void createCartPanel() {
        cartPanel = new JPanel();
        cartPanel.setLayout(new BoxLayout(cartPanel, BoxLayout.Y_AXIS));
        cartPanel.setBorder(BorderFactory.createTitledBorder("Shopping Cart"));
        
        JButton checkoutButton = new JButton("Checkout");
        checkoutButton.addActionListener(e -> handleCheckout());
        cartPanel.add(checkoutButton);
    }

    private void updateCartPanel() {
        cartPanel.removeAll();
        cartPanel.setBorder(BorderFactory.createTitledBorder("Shopping Cart"));
        
        for (Product product : cart.getCartItems()) {
            JPanel itemPanel = new JPanel(new FlowLayout(FlowLayout.LEFT));
            itemPanel.add(new JLabel(product.getName() + " - $" + product.getPrice()));
            JButton removeButton = new JButton("Remove");
            removeButton.addActionListener(e -> {
                cart.removeProductFromCart(product);
                updateCartPanel();
            });
            itemPanel.add(removeButton);
            cartPanel.add(itemPanel);
        }
        
        cartPanel.add(new JLabel("Total: $" + cart.getTotalAmount()));
        JButton checkoutButton = new JButton("Checkout");
        checkoutButton.addActionListener(e -> handleCheckout());
        cartPanel.add(checkoutButton);
        
        cartPanel.revalidate();
        cartPanel.repaint();
    }

    private void handleLogin() {
        String username = usernameField.getText();
        String password = new String(passwordField.getPassword());
        
        // In real app would verify against database
        currentUser = new User(username, password, "Customer");
        if (currentUser.authenticate(password)) {
            currentCustomer = new Customer(1, "John Doe", "john@example.com", "123 Street", currentUser);
            getContentPane().removeAll();
            add(mainPanel);
            mainPanel.setVisible(true);
            revalidate();
            repaint();
        } else {
            JOptionPane.showMessageDialog(this, "Invalid credentials");
        }
    }

    private void handleCheckout() {
        if (cart.getCartItems().isEmpty()) {
            JOptionPane.showMessageDialog(this, "Cart is empty!");
            return;
        }
        
        Order order = new Order(1, currentCustomer, cart.getCartItems());
        JOptionPane.showMessageDialog(this, "Order placed successfully!\n" + order.getOrderDetails());
        cart = new ShoppingCart();
        updateCartPanel();
    }

    public static void main(String[] args) {
        SwingUtilities.invokeLater(() -> {
            new View().setVisible(true);
        });
    }
}
