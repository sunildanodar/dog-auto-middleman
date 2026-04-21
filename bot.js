const { Client, GatewayIntentBits, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, ModalBuilder, TextInputBuilder, TextInputStyle } = require('discord.js');
const { REST } = require('@discordjs/rest');
const { Routes } = require('discord-api-types/v10');
const fs = require('fs');
const axios = require('axios');

// Configuration
const config = {
    TOKEN: process.env.TOKEN || 'YOUR_BOT_TOKEN',
    CLIENT_ID: process.env.CLIENT_ID || 'YOUR_CLIENT_ID',
    GUILD_ID: process.env.GUILD_ID || 'YOUR_GUILD_ID',
    ADMIN_ID: process.env.ADMIN_ID || 'YOUR_ADMIN_ID',
    LOG_CHANNEL_ID: process.env.LOG_CHANNEL_ID || 'YOUR_LOG_CHANNEL_ID',
    TICKET_CATEGORY_ID: process.env.TICKET_CATEGORY_ID || 'YOUR_TICKET_CATEGORY_ID',
    PAYMENT_POLL_INTERVAL: 20000,
    LTC_FEE_BUFFER_SATOSHIS: 10000
};

// Color scheme matching screenshots
const COLORS = {
    primary: 0x2B2D31,
    accent: 0x5865F2,
    success: 0x3BA55C,
    warning: 0xED4245,
    pending: 0xF59E0B,
    detected: 0x5865F2,
    confirming: 0xEB459E,
    confirmed: 0x3BA55C,
    locked: 0x5865F2,
    completed: 0x3BA55C,
    disputed: 0xED4245,
    cancelled: 0x475569
};

// Status system without emojis
const STATUS_CONFIG = {
    pending: { color: COLORS.pending, label: 'Pending' },
    detected: { color: COLORS.detected, label: 'Detected' },
    confirming: { color: COLORS.confirming, label: 'Confirming' },
    confirmed: { color: COLORS.confirmed, label: 'Confirmed' },
    locked: { color: COLORS.locked, label: 'Locked' },
    completed: { color: COLORS.completed, label: 'Completed' },
    disputed: { color: COLORS.disputed, label: 'Disputed' },
    cancelled: { color: COLORS.cancelled, label: 'Cancelled' }
};

// Data storage
const tickets = new Map();
const dealSummaries = new Map();
const activeMonitors = new Set();
const userBlacklist = new Set();
const activeDealLocks = new Set();

// Initialize Discord client
const client = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
    ]
});

// Utility functions
function generateDealId(ticketId) {
    return `DM-${ticketId.toString().padStart(4, '0')}-${Math.floor(Date.now() / 1000) % 10000}`;
}

function getDynamicConfirmations(amount) {
    if (amount < 100) return 1;
    if (amount <= 500) return 2;
    return 3;
}

// Premium UI System - Exact 1:1 Match
class PremiumEmbedBuilder {
    static createMainDealDashboard(ticketId, buyerId, sellerId, amount, crypto, status = 'pending', confirmations = 0, requiredConfs = 1, address = null) {
        const dealId = generateDealId(ticketId);
        const statusConfig = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
        
        const embed = new EmbedBuilder()
            .setTitle(`${dealId}`)
            .setDescription('Secure Escrow Transaction')
            .setColor(statusConfig.color);
        
        // Participants section with exact spacing
        embed.addFields(
            { name: 'Participants', value: `<@${buyerId}>\n\n<@${sellerId}>`, inline: true },
            { name: 'Deal Details', value: `Amount: $${amount.toFixed(2)}\n\nCurrency: ${crypto}`, inline: true }
        );
        
        // Status section with precise spacing
        let statusText;
        if (status === 'confirming') {
            const progressBar = ' '.repeat(confirmations) + ' '.repeat(requiredConfs - confirmations);
            statusText = `${statusConfig.label}\n\n${confirmations}/${requiredConfs} confirmations\n\n\`${progressBar}\``;
        } else if (status === 'detected') {
            statusText = `${statusConfig.label}\n\nPayment detected, verifying...`;
        } else if (status === 'confirmed') {
            statusText = `${statusConfig.label}\n\nFunds secured in escrow`;
        } else {
            statusText = `${statusConfig.label}\n\nWaiting for setup`;
        }
        
        embed.addFields({ name: 'Status', value: statusText, inline: false });
        
        // Payment address when needed
        if (address && ['detected', 'confirming', 'confirmed'].includes(status)) {
            embed.addFields({ name: 'Payment Address', value: `\n\`${address}\``, inline: false });
        }
        
        embed.setFooter({ text: 'Dog Auto Middleman - Secure Escrow Service' })
            .setThumbnail('https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png');
        
        return embed;
    }
    
    static createStatusUpdateEmbed(status, details = '') {
        const statusConfig = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
        
        return new EmbedBuilder()
            .setTitle(statusConfig.label)
            .setDescription(details)
            .setColor(statusConfig.color)
            .setFooter({ text: 'Dog Auto Middleman - Secure Escrow Service' });
    }
    
    static createErrorEmbed(error, guidance = '') {
        const embed = new EmbedBuilder()
            .setTitle('Error')
            .setDescription(error)
            .setColor(COLORS.warning)
            .setFooter({ text: 'Dog Auto Middleman - Secure Escrow Service' });
        
        if (guidance) {
            embed.addFields({ name: 'What to do next', value: guidance, inline: false });
        }
        
        return embed;
    }
    
    static createSuccessEmbed(title, message) {
        return new EmbedBuilder()
            .setTitle(title)
            .setDescription(message)
            .setColor(COLORS.success)
            .setFooter({ text: 'Dog Auto Middleman - Secure Escrow Service' });
    }
    
    static createInstructionEmbed(title, instructions, currentStep = 0) {
        const embed = new EmbedBuilder()
            .setTitle(title)
            .setDescription('Follow these steps to complete your trade:')
            .setColor(COLORS.primary);
        
        instructions.forEach((instruction, i) => {
            const prefix = i < currentStep ? 'Step completed' : i === currentStep ? 'Current step' : 'Pending';
            embed.addFields({ 
                name: `Step ${i + 1}: ${instruction}`, 
                value: prefix, 
                inline: false 
            });
        });
        
        embed.setFooter({ text: 'Dog Auto Middleman - Secure Escrow Service' });
        return embed;
    }
}

// Premium Button System - Exact 1:1 Match
class PremiumButtonSystem {
    static createMainDealView(ticketId, status, userId, buyerId, sellerId) {
        const view = new ActionRowBuilder();
        const buttons = [];
        
        const isBuyer = userId === buyerId;
        const isSeller = userId === sellerId;
        const isAdmin = userId === config.ADMIN_ID;
        
        // Copy buttons in first row
        if (['detected', 'confirming', 'confirmed'].includes(status)) {
            buttons.push(
                new ButtonBuilder()
                    .setLabel('Copy Address')
                    .setStyle(ButtonStyle.Secondary)
                    .setCustomId(`premium_copy_address_${ticketId}`)
            );
            
            buttons.push(
                new ButtonBuilder()
                    .setLabel('Copy Amount')
                    .setStyle(ButtonStyle.Secondary)
                    .setCustomId(`premium_copy_amount_${ticketId}`)
            );
        }
        
        // Action buttons in second row
        if (status === 'confirmed') {
            if (isBuyer || isAdmin) {
                buttons.push(
                    new ButtonBuilder()
                        .setLabel('Release Funds')
                        .setStyle(ButtonStyle.Success)
                        .setCustomId(`premium_release_${ticketId}`)
                );
            }
            
            if (isSeller && !isBuyer) {
                buttons.push(
                    new ButtonBuilder()
                        .setLabel('Withdraw Funds')
                        .setStyle(ButtonStyle.Primary)
                        .setCustomId(`premium_withdraw_${ticketId}`)
                );
            }
        }
        
        // Dispute button in third row
        if (['detected', 'confirming', 'confirmed'].includes(status)) {
            buttons.push(
                new ButtonBuilder()
                    .setLabel('Open Dispute')
                    .setStyle(ButtonStyle.Danger)
                    .setCustomId(`premium_dispute_${ticketId}`)
            );
        }
        
        // Admin cancel button
        if (isAdmin && status !== 'completed') {
            buttons.push(
                new ButtonBuilder()
                    .setLabel('Force Cancel')
                    .setStyle(ButtonStyle.Danger)
                    .setCustomId(`premium_admin_cancel_${ticketId}`)
            );
        }
        
        // Add buttons to view (max 5 per row)
        const rows = [];
        for (let i = 0; i < buttons.length; i += 5) {
            const row = new ActionRowBuilder();
            buttons.slice(i, i + 5).forEach(button => row.addComponents(button));
            rows.push(row);
        }
        
        return rows;
    }
}

// Premium Flow Manager - Exact 1:1 Match
class PremiumFlowManager {
    static async createGuidedSetup(ticketId, channel, buyerId, sellerId, crypto) {
        // Setup message without emojis
        const setupEmbed = new EmbedBuilder()
            .setTitle('Deal Setup')
            .setDescription('Follow these steps to complete your trade:')
            .setColor(COLORS.primary);
        
        setupEmbed.addFields(
            { name: 'Step 1: Select Roles', value: 'Choose who is the buyer and seller', inline: false },
            { name: 'Step 2: Confirm Amount', value: 'Verify the deal amount in USD', inline: false },
            { name: 'Step 3: Send Payment', value: 'Transfer funds to the provided address', inline: false },
            { name: 'Step 4: Complete Trade', value: 'Wait for confirmation and finalize', inline: false }
        );
        
        setupEmbed.setFooter({ text: 'Dog Auto Middleman - Secure Escrow Service' });
        
        // Main deal dashboard
        const dealEmbed = PremiumEmbedBuilder.createMainDealDashboard(
            ticketId, buyerId, sellerId, 0, crypto, 'pending'
        );
        
        // Send messages with exact spacing
        await channel.send({ embeds: [setupEmbed] });
        await channel.send({ content: '\u200b' }); // Perfect spacer
        const dealMessage = await channel.send({
            embeds: [dealEmbed],
            components: PremiumButtonSystem.createMainDealView(ticketId, 'pending', buyerId, buyerId, sellerId)
        });
        
        // Store for updates
        dealSummaries.set(ticketId, dealMessage);
        
        return dealMessage;
    }
    
    static async updateDealDashboard(ticketId, status, options = {}) {
        if (!dealSummaries.has(ticketId)) return;
        
        const ticket = tickets.get(ticketId);
        if (!ticket) return;
        
        const { confirmations = 0, requiredConfs = 1 } = options;
        
        const updatedEmbed = PremiumEmbedBuilder.createMainDealDashboard(
            ticketId, ticket.buyerId, ticket.sellerId, ticket.amount, ticket.crypto, status,
            confirmations, requiredConfs, ticket.address
        );
        
        const updatedComponents = PremiumButtonSystem.createMainDealView(
            ticketId, status, ticket.buyerId, ticket.buyerId, ticket.sellerId
        );
        
        try {
            await dealSummaries.get(ticketId).edit({
                embeds: [updatedEmbed],
                components: updatedComponents
            });
        } catch (error) {
            console.error('Error updating deal dashboard:', error);
        }
    }
    
    static async sendStatusUpdate(channel, status, details = '') {
        const embed = PremiumEmbedBuilder.createStatusUpdateEmbed(status, details);
        await channel.send({ embeds: [embed] });
    }
    
    static async sendErrorMessage(channel, error, guidance = '') {
        const embed = PremiumEmbedBuilder.createErrorEmbed(error, guidance);
        await channel.send({ embeds: [embed] });
    }
    
    static async sendSuccessMessage(channel, title, message) {
        const embed = PremiumEmbedBuilder.createSuccessEmbed(title, message);
        await channel.send({ embeds: [embed] });
    }
}

// Modal for deal creation
class RequestModal extends ModalBuilder {
    constructor(crypto) {
        super()
            .setCustomId(`request_${crypto}`)
            .setTitle(`Create ${crypto} Deal`)
            .addComponents(
                new ActionRowBuilder().addComponents(
                    new TextInputBuilder()
                        .setCustomId('amount')
                        .setLabel('Amount in USD')
                        .setStyle(TextInputStyle.Short)
                        .setRequired(true)
                        .setPlaceholder('Enter amount (e.g., 250.00)')
                ),
                new ActionRowBuilder().addComponents(
                    new TextInputBuilder()
                        .setCustomId('user_id')
                        .setLabel('User ID')
                        .setStyle(TextInputStyle.Short)
                        .setRequired(true)
                        .setPlaceholder('Enter Discord user ID')
                )
            );
    }
}

// Button views for initial setup
class RequestLTCView extends ActionRowBuilder {
    constructor() {
        super();
        this.addComponents(
            new ButtonBuilder()
                .setLabel('Start Trade')
                .setStyle(ButtonStyle.Primary)
                .setCustomId('request_ltc')
        );
    }
}

class RequestUSDTBEP20View extends ActionRowBuilder {
    constructor() {
        super();
        this.addComponents(
            new ButtonBuilder()
                .setLabel('Start Trade')
                .setStyle(ButtonStyle.Success)
                .setCustomId('request_usdt_bep20')
        );
    }
}

class RequestUSDTETHView extends ActionRowBuilder {
    constructor() {
        super();
        this.addComponents(
            new ButtonBuilder()
                .setLabel('Start Trade')
                .setStyle(ButtonStyle.Secondary)
                .setCustomId('request_usdt_eth')
        );
    }
}

// Main panel command
client.on('messageCreate', async (message) => {
    if (message.content === '!panel' || message.content === '!dog_panel') {
        // Main panel embed
        const introEmbed = new EmbedBuilder()
            .setTitle('Dog Auto Middleman')
            .setDescription('Premium Escrow Service')
            .setColor(COLORS.primary);
        
        introEmbed.addFields(
            { name: 'Getting Started', value: 'Read ToS: `# ??tos`\nRules: `# ??mm-tos`', inline: true },
            { name: 'Fee Structure', value: '$250+: $1.50\nUnder $250: $0.50\nUnder $50: FREE', inline: true }
        );
        
        introEmbed.setThumbnail('https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png')
            .setFooter({ text: 'Trusted by 1000+ Traders' });
        
        // Crypto options
        const ltcEmbed = new EmbedBuilder()
            .setTitle('Litecoin')
            .setDescription('Fast - Low fees - ~2.5 min confirmations')
            .setColor(COLORS.accent)
            .setThumbnail('https://cdn.discordapp.com/attachments/814749431716843540/814749431716843544/ltc.png');
        
        const usdtBep20Embed = new EmbedBuilder()
            .setTitle('USDT (BEP-20)')
            .setDescription('Ultra-low fees - ~3 seconds - BSC Network')
            .setColor(COLORS.warning)
            .setThumbnail('https://cdn.discordapp.com/attachments/814749431716843540/814749431716843545/bnb.png');
        
        const usdtEthEmbed = new EmbedBuilder()
            .setTitle('USDT (ERC-20)')
            .setDescription('Widely supported - Ethereum Network')
            .setColor(COLORS.primary)
            .setThumbnail('https://cdn.discordapp.com/attachments/814749431716843540/814749431716843546/eth.png');
        
        // Send with perfect spacing
        await message.channel.send({ embeds: [introEmbed] });
        await message.channel.send({ content: '\u200b' });
        await message.channel.send({ embeds: [ltcEmbed], components: [new RequestLTCView()] });
        await message.channel.send({ embeds: [usdtBep20Embed], components: [new RequestUSDTBEP20View()] });
        await message.channel.send({ embeds: [usdtEthEmbed], components: [new RequestUSDTETHView()] });
    }
});

// Interaction handler
client.on('interactionCreate', async (interaction) => {
    if (interaction.isButton()) {
        const customId = interaction.customId;
        
        if (customId === 'request_ltc') {
            await interaction.showModal(new RequestModal('LTC'));
        } else if (customId === 'request_usdt_bep20') {
            await interaction.showModal(new RequestModal('USDT_BEP20'));
        } else if (customId === 'request_usdt_eth') {
            await interaction.showModal(new RequestModal('USDT_ETH'));
        }
        // Handle other button interactions...
    } else if (interaction.isModalSubmit()) {
        const crypto = interaction.customId.replace('request_', '');
        const amount = parseFloat(interaction.fields.getTextInputValue('amount'));
        const userId = interaction.fields.getTextInputValue('user_id').replace(/[<@!>]/g, '');
        
        // Create ticket and setup flow
        const ticketId = Date.now();
        const buyerId = interaction.user.id;
        const sellerId = userId;
        
        // Store ticket data
        tickets.set(ticketId, {
            id: ticketId,
            buyerId,
            sellerId,
            amount,
            crypto,
            status: 'pending',
            address: null,
            channelId: null,
            messageId: null
        });
        
        // Create ticket channel (simplified)
        const channel = interaction.channel;
        
        // Start guided setup
        await PremiumFlowManager.createGuidedSetup(ticketId, channel, buyerId, sellerId, crypto);
        
        await interaction.reply({ content: `Ticket created! Deal ID: ${generateDealId(ticketId)}`, ephemeral: true });
    }
});

// Bot ready event
client.once('ready', () => {
    console.log(`Bot is online as ${client.user.tag}`);
    
    // Register slash commands
    const commands = [
        {
            name: 'panel',
            description: 'Show the Dog Auto Middleman panel'
        }
    ];
    
    const rest = new REST({ version: '10' }).setToken(config.TOKEN);
    
    try {
        console.log('Started refreshing application (/) commands.');
        
        if (config.GUILD_ID) {
            await rest.put(
                Routes.applicationGuildCommands(config.CLIENT_ID, config.GUILD_ID),
                { body: commands }
            );
        } else {
            await rest.put(
                Routes.applicationCommands(config.CLIENT_ID),
                { body: commands }
            );
        }
        
        console.log('Successfully reloaded application (/) commands.');
    } catch (error) {
        console.error(error);
    }
});

// Error handling
client.on('error', (error) => {
    console.error('Discord client error:', error);
});

process.on('unhandledRejection', (error) => {
    console.error('Unhandled promise rejection:', error);
});

// Login bot
client.login(config.TOKEN);

module.exports = { client, config, PremiumEmbedBuilder, PremiumButtonSystem, PremiumFlowManager };
